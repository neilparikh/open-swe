"""Teams messages start and continue agent runs.

A direct message with the bot is one ongoing agent thread, private to the
person, until they type ``new`` or ``start over``. An @mention in a team channel
runs in the agent thread of that Teams thread, which everyone can open, as Slack
channel threads are. Every run acts as the linked person who sent the message;
group chats only get a pointer.
"""

import html
import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from http import HTTPStatus
from typing import Any, Literal, Self

from langgraph_sdk.client import LangGraphClient
from microsoft_agents.activity import Activity, ActivityTypes, AdaptiveCardInvokeResponse
from microsoft_agents.hosting.core import TurnContext, TurnState

from openswe import event_claims
from openswe.dashboard.oauth import build_settings_url
from openswe.dispatch import thread_workspace
from openswe.input_messages import RunInput, build_input_messages
from openswe.source_context import SourceContext, TeamsConversationRef
from openswe.teams.cards import answered_card
from openswe.teams.conversations import TeamsConversation
from openswe.users import User
from openswe.utils.json_types import JsonObject, thread_metadata
from openswe.utils.thread_ops import cancel_active_runs, langgraph_client
from openswe.webhooks import common
from openswe.workspaces.routing import resolve_workspace

logger = logging.getLogger(__name__)

_CLAIM_SCOPE = "teams-activity"
_CLAIM_TTL = timedelta(hours=1)
# A card's buttons stay on screen long after the run that offered them.
_ANSWER_CLAIM_SCOPE = "teams-answer"
_ANSWER_CLAIM_TTL = timedelta(days=7)
_CARD_RESPONSE = "application/vnd.microsoft.card.adaptive"
_MESSAGE_RESPONSE = "application/vnd.microsoft.activity.message"
_START_OVER_COMMANDS = frozenset({"new", "start over"})
_TITLE_MAX_CHARS = 80

_MENTION = re.compile(r"<at>(.*?)</at>", re.DOTALL)

NOT_A_CHANNEL_OR_DIRECT_MESSAGE = "Mention me in a team channel or message me directly."
STARTED_OVER = "Started a new conversation. What should I work on?"
ALREADY_ANSWERED = "This was already answered."
STALE_BUTTON = "That button no longer works here."

ConversationKind = Literal["direct", "channel", "other"]


@dataclass(frozen=True)
class TeamsMessage:
    """One inbound Teams message, as the run path needs it."""

    kind: ConversationKind
    reference: TeamsConversationRef
    text: str
    sender_object_id: str
    sender_name: str

    @classmethod
    def parse(cls, activity: Activity, *, text: str | None = None) -> Self:
        """The message ``activity`` carries; ``text`` stands in for a button click's choice."""
        conversation = activity.conversation
        sender = activity.from_property
        conversation_type = (conversation.conversation_type if conversation else None) or ""
        kind: ConversationKind = (
            "direct"
            if conversation_type == "personal"
            else "channel"
            if conversation_type == "channel"
            else "other"
        )
        sender_object_id = (sender.aad_object_id if sender is not None else None) or ""
        reference = TeamsConversationRef(
            service_url=activity.service_url or "",
            conversation_id=conversation.id if conversation is not None else "",
            conversation_type=conversation_type,
            tenant_id=(conversation.tenant_id if conversation is not None else None) or "",
            bot_id=activity.recipient.id if activity.recipient is not None else "",
            user_id=sender.id if sender is not None else "",
            user_aad_object_id=sender_object_id,
        )
        return cls(
            kind,
            reference,
            _plain_text(activity.text or "") if text is None else text.strip(),
            sender_object_id,
            (sender.name if sender is not None else None) or "",
        )

    @property
    def starts_over(self) -> bool:
        """A channel thread is its Teams thread; only a direct message can start over."""
        return self.kind == "direct" and self.text.lower() in _START_OVER_COMMANDS

    async def start_run(self, conversation: TeamsConversation, user: User) -> None:
        """Run this message in the conversation's thread, as ``user``."""
        login = user.github_login
        thread_id = conversation.thread_id
        direct = self.kind == "direct"
        client = langgraph_client()
        metadata = await _existing_metadata(client, thread_id)
        thread_repo = common.repo_config_from_thread({"metadata": metadata}) if metadata else None
        repo = (
            thread_repo
            or await common.get_profile_default_repo(login)
            or (await common.get_workspace_settings()).default_repo
        )
        workspace = (
            await resolve_workspace(
                thread_workspace=thread_workspace(metadata) if metadata else None,
                repo=(thread_repo["owner"], thread_repo["name"]) if thread_repo else None,
                login=login,
            )
        ).slug
        persisted = await common.upsert_agent_thread_metadata(
            thread_id,
            source="teams",
            repo_config=repo,
            github_login=login,
            user_email=user.email,
            title="" if metadata else _title(self.text),
            source_context=SourceContext(teams_conversation=self.reference),
            workspace=workspace,
            # A channel thread is collaborative, as Slack channel threads are.
            visibility="private" if direct else "public",
            owner_login=login,
        )
        if not persisted:
            # Dispatch would create the thread itself, with no metadata and so public.
            raise RuntimeError("could not persist thread authorization metadata")

        configurable: dict[str, Any] = {
            "source": "teams",
            "github_login": login,
            "user_email": user.email,
            "workspace": workspace,
            "environment": workspace,
            "teams_conversation": self.reference.dump(),
        }
        if direct:
            # A direct message reaches exactly one person; the agent still rechecks
            # them against the configured admins before handing out admin tools.
            configurable["admin_thread"] = True
        if repo:
            configurable["repo"] = repo
        sender_id = user.as_person({"id": f"github:{login}", "github_login": login})["id"]
        run_input: RunInput = {
            "messages": build_input_messages(
                self.text, {"sender_id": sender_id, "surface": "teams", "kind": "human"}
            )
        }
        # Every message that reaches the bot is addressed to it, so it interrupts.
        run = await common.dispatch_agent_run(
            thread_id,
            None,
            configurable,
            source="teams",
            thread_title=None,
            input=run_input,
            metadata=common.AGENT_VERSION_METADATA,
            client=client,
            multitask_strategy="interrupt",
        )
        logger.info(
            "Dispatched a run for a Teams message",
            extra={
                "agent_thread_id": thread_id,
                "agent_run_id": common.run_id_for_logging(run),
                "teams_conversation_kind": self.kind,
            },
        )


async def handle_message(context: TurnContext, _state: TurnState) -> None:
    """Answer one Teams message, exactly once even if Bot Framework redelivers it."""
    activity = context.activity
    if activity.id and not await event_claims.claim(_CLAIM_SCOPE, activity.id, ttl=_CLAIM_TTL):
        logger.info(
            "Ignoring a redelivered Teams message", extra={"teams_activity_id": activity.id}
        )
        return
    try:
        await _handle(context)
    except Exception:
        if activity.id:
            await event_claims.release(_CLAIM_SCOPE, activity.id)
        raise


async def handle_answer(
    context: TurnContext, _state: TurnState, data: object
) -> AdaptiveCardInvokeResponse:
    """Run an answer button's choice as the clicker's next message, then retire the card.

    Each card is answered once: a second click, or a teammate clicking the same
    card in a channel, starts nothing.
    """
    answer, card_id = _answer(data)
    message = TeamsMessage.parse(context.activity, text=answer)
    if not message.text or not card_id or message.kind == "other":
        return _invoke_message(STALE_BUTTON)
    try:
        user = await _linked_sender(message)
    except SenderNotReady as exc:
        # The card stays answerable, so they can link their account and click again.
        return _invoke_message(exc.prompt)
    if not await event_claims.claim(_ANSWER_CLAIM_SCOPE, card_id, ttl=_ANSWER_CLAIM_TTL):
        return _invoke_message(ALREADY_ANSWERED)
    try:
        conversation = await TeamsConversation.current(message.reference.conversation_id)
        await message.start_run(conversation, user)
    except Exception:
        await event_claims.release(_ANSWER_CLAIM_SCOPE, card_id)
        raise
    return AdaptiveCardInvokeResponse(
        status_code=HTTPStatus.OK,
        type=_CARD_RESPONSE,
        value=answered_card(message.text, message.sender_name),
    )


class SenderNotReady(Exception):
    """The sender cannot start a run yet; ``prompt`` tells them what to do about it."""

    def __init__(self, prompt: str) -> None:
        super().__init__(prompt)
        self.prompt = prompt


async def _linked_sender(message: TeamsMessage) -> User:
    """The Open SWE user who sent ``message``, ready to run as."""
    user = (
        await User.for_identity("microsoft", message.sender_object_id)
        if message.sender_object_id
        else None
    )
    if user is None or not user.github_login:
        raise SenderNotReady(_link_prompt())
    if not await _has_github_token(user.github_login):
        raise SenderNotReady(_sign_in_again_prompt())
    return user


async def _handle(context: TurnContext) -> None:
    message = TeamsMessage.parse(context.activity)
    if message.kind == "other":
        await context.send_activity(NOT_A_CHANNEL_OR_DIRECT_MESSAGE)
        return
    try:
        user = await _linked_sender(message)
    except SenderNotReady as exc:
        await context.send_activity(exc.prompt)
        return

    conversation = await TeamsConversation.current(message.reference.conversation_id)
    if message.starts_over:
        await cancel_active_runs(conversation.thread_id)
        await conversation.start_over()
        await context.send_activity(STARTED_OVER)
        return
    if not message.text:
        return
    if message.kind == "direct":
        # Teams shows typing in chats only, not in channel threads.
        await context.send_activity(Activity(type=ActivityTypes.typing))
    await message.start_run(conversation, user)


async def _existing_metadata(client: LangGraphClient, thread_id: str) -> JsonObject | None:
    """The thread's metadata, or ``None`` when this message starts it."""
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:
        if common.is_not_found_error(exc):
            return None
        raise
    return thread_metadata(thread)


async def _has_github_token(login: str) -> bool:
    try:
        return bool(await common.get_valid_access_token(login))
    except Exception:  # noqa: BLE001
        # Treated as missing: the person is asked to sign in again, which fixes
        # a stale token and is harmless otherwise.
        logger.warning(
            "Could not read a GitHub token for a Teams sender",
            extra={"github_login": login},
            exc_info=True,
        )
        return False


def _answer(data: object) -> tuple[str, str]:
    """``(answer, card id)`` from an answer button's data, empty when malformed."""
    if not isinstance(data, dict):
        return "", ""
    answer = data.get("answer")
    card_id = data.get("card")
    return (
        answer.strip() if isinstance(answer, str) else "",
        card_id if isinstance(card_id, str) else "",
    )


def _invoke_message(text: str) -> AdaptiveCardInvokeResponse:
    """A short note Teams shows the clicker, leaving the card as it is."""
    return AdaptiveCardInvokeResponse(status_code=HTTPStatus.OK, type=_MESSAGE_RESPONSE, value=text)


def _plain_text(text: str) -> str:
    """Teams message text as the agent reads it: other mentions as ``@Name``, entities decoded.

    The SDK has already removed the mention of the bot itself.
    """
    return html.unescape(_MENTION.sub(r"@\1", text)).strip()


def _title(text: str) -> str:
    first_line = text.splitlines()[0].strip()
    if len(first_line) <= _TITLE_MAX_CHARS:
        return first_line
    return first_line[: _TITLE_MAX_CHARS - 1].rstrip() + "…"


def _link_prompt() -> str:
    # Token-free and the same for everyone, so it is safe wherever it is posted.
    settings_url = build_settings_url()
    if settings_url is None:
        return "I don't know who you are yet. Ask your Open SWE admin how to link your account."
    return (
        "I don't know who you are yet. Connect Microsoft Teams in "
        f"[your Open SWE settings]({settings_url}), then message me again."
    )


def _sign_in_again_prompt() -> str:
    settings_url = build_settings_url()
    if settings_url is None:
        return "Your GitHub sign-in has expired. Sign in to Open SWE again, then message me."
    return (
        "Your GitHub sign-in has expired. Sign in to "
        f"[Open SWE]({settings_url}) again, then message me."
    )
