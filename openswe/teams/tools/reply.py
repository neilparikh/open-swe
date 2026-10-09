"""The `teams_reply` tool: the agent's words to the person in a Teams direct message."""

import logging
from typing import Any, Literal

from langgraph.config import get_config

from openswe.run_config import RunConfig
from openswe.teams.bot import TeamsBot, TeamsDeliveryRefused
from openswe.teams.cards import answer_card

logger = logging.getLogger(__name__)

# Teams refuses messages past roughly 28 KB; the prompt asks for far less.
_MAX_MESSAGE_CHARS = 20_000


async def teams_reply(
    message: str,
    response_type: Literal["progress", "final"],
    options: list[str] | None = None,
) -> dict[str, Any]:
    """Implement the `teams_reply` tool."""
    text = message.strip()
    if not text:
        return {"success": False, "error": "message is empty"}
    cfg = RunConfig.from_config(get_config())
    if cfg.teams_conversation is None:
        return {"success": False, "error": "this thread has no Teams conversation to reply in"}
    bot = TeamsBot.configured()
    if bot is None:
        return {"success": False, "error": "Microsoft Teams is not configured"}
    if len(text) > _MAX_MESSAGE_CHARS:
        text = text[: _MAX_MESSAGE_CHARS - 1].rstrip() + "…"
    try:
        await bot.send(cfg.teams_conversation, text, card=answer_card(options or []))
    except TeamsDeliveryRefused as exc:
        logger.warning(
            "Refused a Teams reply",
            extra={"agent_thread_id": cfg.thread_id, "teams_error": str(exc)},
        )
        return {"success": False, "error": str(exc)}
    except Exception as exc:
        logger.exception("Failed to post a Teams reply", extra={"agent_thread_id": cfg.thread_id})
        return {"success": False, "error": f"Teams did not accept the reply: {type(exc).__name__}"}
    return {"success": True, "response_type": response_type}
