"""Teams direct messages start and continue agent runs.

A 1:1 chat with the bot is one ongoing agent thread that runs as the person
behind the linked Microsoft account; ``new`` or ``start over`` begins a fresh
one. Channels and group chats only get a pointer to the direct message.
"""

import logging
from datetime import timedelta
from typing import Any

from langgraph_sdk.client import LangGraphClient
from microsoft_agents.activity import Activity, ActivityTypes
from microsoft_agents.hosting.core import TurnContext, TurnState

from openswe import event_claims
from openswe.dashboard.oauth import build_settings_url
from openswe.dispatch import thread_workspace
from openswe.input_messages import RunInput, build_input_messages
from openswe.source_context import SourceContext, TeamsConversationRef
from openswe.teams.conversations import TeamsConversation
from openswe.users import User
from openswe.utils.json_types import JsonObject, thread_metadata
from openswe.utils.thread_ops import cancel_active_runs, langgraph_client
from openswe.webhooks import common
from openswe.workspaces.routing import resolve_workspace

logger = logging.getLogger(__name__)

_CLAIM_SCOPE = "teams-activity"
_CLAIM_TTL = timedelta(hours=1)
_START_OVER_COMMANDS = frozenset({"new", "start over"})
_TITLE_MAX_CHARS = 80

DIRECT_MESSAGE_ONLY = "I only take requests in a direct message for now. Message me there."
STARTED_OVER = "Started a new conversation. What should I work on?"


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


async def _handle(context: TurnContext) -> None:
    activity = context.activity
    conversation = activity.conversation
    if conversation is None or conversation.conversation_type != "personal":
        await context.send_activity(DIRECT_MESSAGE_ONLY)
        return

    sender = activity.from_property
    object_id = sender.aad_object_id if sender is not None else None
    user = await User.for_identity("microsoft", object_id) if object_id else None
    if user is None or not user.github_login:
        await context.send_activity(_link_prompt())
        return
    login = user.github_login
    if not await _has_github_token(login):
        await context.send_activity(_sign_in_again_prompt())
        return

    text = (activity.text or "").strip()
    thread = await TeamsConversation.current(conversation.id)
    if text.lower() in _START_OVER_COMMANDS:
        await cancel_active_runs(thread.thread_id)
        await thread.start_over()
        await context.send_activity(STARTED_OVER)
        return
    if not text:
        return

    await context.send_activity(Activity(type=ActivityTypes.typing))
    reference = TeamsConversationRef(
        service_url=activity.service_url or "",
        conversation_id=conversation.id,
        tenant_id=conversation.tenant_id or "",
        bot_id=activity.recipient.id if activity.recipient is not None else "",
        user_id=sender.id if sender is not None else "",
        user_aad_object_id=object_id or "",
    )
    await _dispatch(thread, reference, user, login, text)


async def _dispatch(
    conversation: TeamsConversation,
    reference: TeamsConversationRef,
    user: User,
    login: str,
    text: str,
) -> None:
    thread_id = conversation.thread_id
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
        title="" if metadata else _title(text),
        source_context=SourceContext(teams_conversation=reference),
        workspace=workspace,
        visibility="private",
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
        "teams_conversation": reference.dump(),
        # A direct message reaches exactly one person; the agent still rechecks
        # them against the configured admins before handing out admin tools.
        "admin_thread": True,
    }
    if repo:
        configurable["repo"] = repo
    sender_id = user.as_person({"id": f"github:{login}", "github_login": login})["id"]
    run_input: RunInput = {
        "messages": build_input_messages(
            text, {"sender_id": sender_id, "surface": "teams", "kind": "human"}
        )
    }
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
        "Dispatched a run for a Teams direct message",
        extra={"agent_thread_id": thread_id, "agent_run_id": common.run_id_for_logging(run)},
    )


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
