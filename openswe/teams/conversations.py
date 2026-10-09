"""Which agent thread a Teams direct message continues.

A 1:1 chat with the bot has no reply threads, so the whole chat is one ongoing
agent thread until the person starts over. The thread id derives from the
conversation and its generation, so starting over only bumps the generation.
"""

from dataclasses import dataclass
from typing import Self

from sqlalchemy import TextClause, text

from openswe.database.postgres import transaction
from openswe.thread_ids import teams_thread_id

_CURRENT = text(
    """
    INSERT INTO teams_conversation (conversation_id) VALUES (:conversation_id)
    ON CONFLICT (conversation_id) DO UPDATE SET updated_at = clock_timestamp()
    RETURNING generation
    """
)
_START_OVER = text(
    """
    INSERT INTO teams_conversation (conversation_id, generation) VALUES (:conversation_id, 1)
    ON CONFLICT (conversation_id) DO UPDATE
        SET generation = teams_conversation.generation + 1, updated_at = clock_timestamp()
    RETURNING generation
    """
)


@dataclass(frozen=True)
class TeamsConversation:
    conversation_id: str
    generation: int

    @classmethod
    async def current(cls, conversation_id: str) -> Self:
        return cls(conversation_id, await _generation(_CURRENT, conversation_id))

    @property
    def thread_id(self) -> str:
        return teams_thread_id(self.conversation_id, self.generation)

    async def start_over(self) -> Self:
        """The conversation's next generation, whose thread starts empty."""
        return type(self)(
            self.conversation_id, await _generation(_START_OVER, self.conversation_id)
        )


async def _generation(statement: TextClause, conversation_id: str) -> int:
    async with transaction() as conn:
        result = await conn.execute(statement, {"conversation_id": conversation_id})
        return int(result.scalar_one())
