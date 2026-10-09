"""Teams deliveries reach the bot only verified, and only linked people start runs or answer cards."""

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from langgraph_runtime_inmem.queue import _enable_blockbuster
from microsoft_agents.activity import Activity
from microsoft_agents.authentication.msal import msal_auth
from microsoft_agents.hosting.core import (
    ClaimsIdentity,
    JwtTokenValidator,
    TurnContext,
    TurnState,
)
from microsoft_agents.hosting.fastapi import CloudAdapter

from openswe.source_context import TeamsConversationRef
from openswe.teams import bot, runs
from openswe.teams.cards import ANSWER_VERB
from openswe.teams.routes import router
from openswe.teams.tools import reply
from openswe.users import User, UserIdentity
from openswe.webhooks import common
from openswe.workspaces.routing import WorkspaceResolution

_CLIENT_ID = "6a1f0c52-3c39-4a39-9a8e-2c5a3a0f4b11"
_TENANT_ID = "0b9f1e2d-7c6a-4f5e-8d3c-2b1a09f8e7d6"
_TEAMS_SERVICE_URL = "https://smba.trafficmanager.net/amer/"
_ALICE_OBJECT_ID = "alice-object-id"
_DIRECT_MESSAGE = "a:alice-and-the-bot"
_CHANNEL = "19:engineering@thread.tacv2"


@pytest.fixture(autouse=True)
def teams_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TEAMS_CLIENT_ID", _CLIENT_ID)
    monkeypatch.setenv("TEAMS_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("TEAMS_TENANT_ID", _TENANT_ID)
    yield
    # The bot caches its MSAL client, which a test may have built from a fake.
    bot._bot.cache_clear()


@pytest.fixture
def process_activity(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    turn = AsyncMock()
    monkeypatch.setattr(CloudAdapter, "process_activity", turn)
    return turn


@dataclass
class Platform:
    """The run path's outer edges, faked; what the handler decided lands here."""

    dispatch: AsyncMock
    upsert: AsyncMock
    cancel: AsyncMock


@pytest.fixture
def platform(monkeypatch: pytest.MonkeyPatch, registry_db: None) -> Platform:
    alice = User(identities=[UserIdentity(provider="github", external_id="1", login="alice")])

    async def linked_user(provider: str, external_id: str) -> User | None:
        return alice if (provider, external_id) == ("microsoft", _ALICE_OBJECT_ID) else None

    async def no_thread_yet(thread_id: str) -> None:
        raise _NotFound(thread_id)

    monkeypatch.setattr(User, "for_identity", linked_user)
    monkeypatch.setattr(common, "get_valid_access_token", AsyncMock(return_value="gho_alice"))
    monkeypatch.setattr(common, "get_profile_default_repo", AsyncMock(return_value=None))
    monkeypatch.setattr(
        common, "get_workspace_settings", AsyncMock(return_value=SimpleNamespace(default_repo=None))
    )
    monkeypatch.setattr(
        runs,
        "langgraph_client",
        lambda: SimpleNamespace(threads=SimpleNamespace(get=no_thread_yet)),
    )
    monkeypatch.setattr(
        runs,
        "resolve_workspace",
        AsyncMock(return_value=WorkspaceResolution("default", "instance_default")),
    )
    platform = Platform(
        dispatch=AsyncMock(return_value={"run_id": "run-1"}),
        upsert=AsyncMock(return_value=True),
        cancel=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(common, "dispatch_agent_run", platform.dispatch)
    monkeypatch.setattr(common, "upsert_agent_thread_metadata", platform.upsert)
    monkeypatch.setattr(runs, "cancel_active_runs", platform.cancel)
    return platform


class _NotFound(Exception):
    status_code = 404


class _Context:
    """The part of a ``TurnContext`` the message handler uses."""

    def __init__(self, activity: Activity) -> None:
        self.activity = activity
        self.sent: list[object] = []

    async def send_activity(self, activity_or_text: object) -> None:
        self.sent.append(activity_or_text)

    @property
    def texts(self) -> list[str]:
        return [sent for sent in self.sent if isinstance(sent, str)]


def _activity_json(
    *,
    activity_id: str = "1",
    text: str = "what's in the repo?",
    service_url: str = _TEAMS_SERVICE_URL,
    sender_object_id: str = _ALICE_OBJECT_ID,
    conversation_type: str = "personal",
    conversation_id: str = _DIRECT_MESSAGE,
) -> dict[str, Any]:
    return {
        "type": "message",
        "id": activity_id,
        "channelId": "msteams",
        "serviceUrl": service_url,
        "from": {"id": "29:alice", "name": "Alice", "aadObjectId": sender_object_id},
        "recipient": {"id": f"28:{_CLIENT_ID}"},
        "conversation": {
            "id": conversation_id,
            "tenantId": _TENANT_ID,
            "conversationType": conversation_type,
        },
        "text": text,
    }


async def _message(**fields: Any) -> _Context:
    context = _Context(Activity.model_validate(_activity_json(**fields)))
    await runs.handle_message(cast(TurnContext, context), cast(TurnState, None))
    return context


def _dispatched_thread(platform: Platform, call: int = -1) -> str:
    return platform.dispatch.await_args_list[call].args[0]


@contextmanager
def langgraph_dev_blocking_checks() -> Iterator[None]:
    """The blocking-call detector exactly as ``langgraph dev`` configures it."""
    detector = _enable_blockbuster()
    try:
        yield
    finally:
        detector.deactivate()


class _BlockingMsalClient:
    """Stands in for MSAL's client, whose constructor fetches tenant metadata synchronously."""

    def __init__(self, **_: object) -> None:
        time.sleep(0)

    def acquire_token_for_client(self, scopes: list[str]) -> dict[str, object]:
        return {"access_token": "bot-token", "expires_in": 3600}


def _verified_as_microsoft(monkeypatch: pytest.MonkeyPatch, service_url: str) -> None:
    identity = ClaimsIdentity({"aud": _CLIENT_ID, "serviceurl": service_url})
    monkeypatch.setattr(JwtTokenValidator, "validate_token", AsyncMock(return_value=identity))


async def _post(body: bytes, authorization: str | None) -> httpx.Response:
    app = FastAPI()
    app.include_router(router)
    headers = {"Content-Type": "application/json"}
    if authorization is not None:
        headers["Authorization"] = authorization
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post("/webhooks/teams", content=body, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization", [None, "Bearer not-a-jwt", "Basic dXNlcjpwYXNz"])
async def test_teams_webhook_rejects_unverified_deliveries(
    process_activity: AsyncMock, authorization: str | None
) -> None:
    response = await _post(json.dumps(_activity_json()).encode(), authorization)
    assert response.status_code == 401
    process_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_teams_webhook_never_replies_to_a_non_microsoft_service_url(
    process_activity: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    service_url = "https://attacker.example/"
    _verified_as_microsoft(monkeypatch, service_url)

    response = await _post(
        json.dumps(_activity_json(service_url=service_url)).encode(), "Bearer signed-by-microsoft"
    )

    assert response.status_code == 401
    assert "host validator" in response.text
    process_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_teams_webhook_starts_a_run_without_blocking_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, platform: Platform
) -> None:
    sent: list[object] = []

    async def send_activity(_context: TurnContext, activity: object, *_: object) -> None:
        sent.append(activity)

    _verified_as_microsoft(monkeypatch, _TEAMS_SERVICE_URL)
    monkeypatch.setattr(msal_auth, "ConfidentialClientApplication", _BlockingMsalClient)
    monkeypatch.setattr(TurnContext, "send_activity", send_activity)

    with langgraph_dev_blocking_checks():
        response = await _post(json.dumps(_activity_json()).encode(), "Bearer signed-by-microsoft")

    assert response.status_code == 202
    platform.dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_linked_direct_message_runs_privately_in_one_ongoing_thread(
    platform: Platform,
) -> None:
    await _message(activity_id="1")
    await _message(activity_id="2", text="and the tests?")

    assert _dispatched_thread(platform, 0) == _dispatched_thread(platform, 1)
    first = platform.dispatch.await_args_list[0]
    assert first.kwargs["multitask_strategy"] == "interrupt"
    configurable = first.args[2]
    assert configurable["source"] == "teams"
    assert configurable["github_login"] == "alice"
    assert TeamsConversationRef.model_validate(configurable["teams_conversation"]) == (
        TeamsConversationRef(
            service_url=_TEAMS_SERVICE_URL,
            conversation_id=_DIRECT_MESSAGE,
            conversation_type="personal",
            tenant_id=_TENANT_ID,
            bot_id=f"28:{_CLIENT_ID}",
            user_id="29:alice",
            user_aad_object_id=_ALICE_OBJECT_ID,
        )
    )
    written = platform.upsert.await_args_list[0].kwargs
    assert (written["visibility"], written["owner_login"]) == ("private", "alice")


@pytest.mark.asyncio
async def test_a_redelivered_message_runs_once(platform: Platform) -> None:
    await _message(activity_id="1")
    await _message(activity_id="1")

    platform.dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_unpersisted_thread_metadata_starts_no_run(platform: Platform) -> None:
    platform.upsert.return_value = False

    with pytest.raises(RuntimeError):
        await _message(activity_id="1")
    platform.dispatch.assert_not_awaited()

    platform.upsert.return_value = True
    await _message(activity_id="1")
    platform.dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_starting_over_stops_the_run_and_moves_to_a_new_thread(platform: Platform) -> None:
    await _message(activity_id="1")
    first_thread = _dispatched_thread(platform)

    reset = await _message(activity_id="2", text="  Start over ")

    platform.cancel.assert_awaited_once_with(first_thread)
    assert reset.texts == [runs.STARTED_OVER]
    platform.dispatch.assert_awaited_once()

    await _message(activity_id="3", text="new question")
    assert _dispatched_thread(platform) != first_thread


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fields", "token", "reply"),
    [
        pytest.param(
            {"conversation_type": "groupChat"}, "gho_alice", "team channel", id="group-chat"
        ),
        pytest.param(
            {"sender_object_id": "someone-else"}, "gho_alice", "who you are", id="unlinked"
        ),
        pytest.param({}, None, "sign-in has expired", id="no-github-token"),
    ],
)
async def test_only_linked_people_in_direct_messages_and_channels_start_runs(
    platform: Platform,
    monkeypatch: pytest.MonkeyPatch,
    fields: dict[str, str],
    token: str | None,
    reply: str,
) -> None:
    monkeypatch.setattr(common, "get_valid_access_token", AsyncMock(return_value=token))

    context = await _message(**fields)

    platform.dispatch.assert_not_awaited()
    assert len(context.texts) == 1 and reply in context.texts[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "service_url", ["https://attacker.example/", "http://smba.trafficmanager.net/amer/"]
)
async def test_replies_never_leave_microsofts_bot_framework_hosts(
    monkeypatch: pytest.MonkeyPatch, service_url: str
) -> None:
    token_clients: list[object] = []

    class _RecordingMsalClient:
        def __init__(self, **_: object) -> None:
            token_clients.append(self)

        def acquire_token_for_client(self, scopes: list[str]) -> dict[str, object]:
            return {"access_token": "bot-token", "expires_in": 3600}

    monkeypatch.setattr(msal_auth, "ConfidentialClientApplication", _RecordingMsalClient)
    teams_bot = bot.TeamsBot.configured()
    assert teams_bot is not None

    with pytest.raises(bot.TeamsDeliveryRefused):
        await teams_bot.send(
            TeamsConversationRef(
                service_url=service_url,
                conversation_id=_DIRECT_MESSAGE,
                bot_id=f"28:{_CLIENT_ID}",
            ),
            "hi",
        )
    assert token_clients == []


def _channel_message(root: str, **fields: Any) -> dict[str, Any]:
    return {
        "conversation_type": "channel",
        "conversation_id": f"{_CHANNEL};messageid={root}",
        **fields,
    }


@pytest.mark.asyncio
async def test_a_channel_mention_runs_publicly_in_its_teams_thread(platform: Platform) -> None:
    mention = await _message(
        **_channel_message("100", activity_id="100", text="<at>Bob</at> needs the build log")
    )
    await _message(**_channel_message("100", activity_id="101", text="and the tests?"))
    await _message(**_channel_message("200", activity_id="200", text="a different post"))

    assert _dispatched_thread(platform, 0) == _dispatched_thread(platform, 1)
    assert _dispatched_thread(platform, 2) != _dispatched_thread(platform, 0)
    first = platform.dispatch.await_args_list[0]
    configurable = first.args[2]
    assert "admin_thread" not in configurable
    assert configurable["teams_conversation"]["conversation_type"] == "channel"
    assert "@Bob needs the build log" in str(first.kwargs["input"]["messages"][-1]["content"])
    written = platform.upsert.await_args_list[0].kwargs
    assert (written["visibility"], written["owner_login"]) == ("public", "alice")
    assert mention.sent == [], "a channel thread gets no typing indicator"


@pytest.mark.asyncio
async def test_start_over_in_a_channel_is_an_ordinary_request(platform: Platform) -> None:
    context = await _message(**_channel_message("100", text="start over"))

    platform.cancel.assert_not_awaited()
    platform.dispatch.assert_awaited_once()
    assert context.texts == []


def _click_json(card_id: str, answer: str, **fields: Any) -> dict[str, Any]:
    return {
        **_activity_json(text="", **fields),
        "type": "invoke",
        "name": "adaptiveCard/action",
        "value": {
            "action": {
                "type": "Action.Execute",
                "verb": ANSWER_VERB,
                "data": {"answer": answer, "card": card_id},
            }
        },
    }


async def _click(card_id: str, answer: str, **fields: Any) -> Any:
    activity = Activity.model_validate(_click_json(card_id, answer, **fields))
    context = _Context(activity)
    data = {"answer": answer, "card": card_id}
    return await runs.handle_answer(cast(TurnContext, context), cast(TurnState, None), data)


@pytest.mark.asyncio
async def test_an_answer_click_runs_as_the_clicker_and_retires_the_card(
    monkeypatch: pytest.MonkeyPatch, platform: Platform
) -> None:
    _verified_as_microsoft(monkeypatch, _TEAMS_SERVICE_URL)
    monkeypatch.setattr(msal_auth, "ConfidentialClientApplication", _BlockingMsalClient)

    response = await _post(
        json.dumps(_click_json("card-1", "Ready for review", activity_id="click-1")).encode(),
        "Bearer signed-by-microsoft",
    )

    assert response.status_code == 200
    assert response.json()["type"] == "application/vnd.microsoft.card.adaptive"
    assert "**Alice** chose **Ready for review**" in json.dumps(response.json()["value"])
    platform.dispatch.assert_awaited_once()
    assert "Ready for review" in str(
        platform.dispatch.await_args.kwargs["input"]["messages"][-1]["content"]
    )


@pytest.mark.asyncio
async def test_each_answer_card_is_answered_once_and_only_by_linked_people(
    platform: Platform,
) -> None:
    stranger = await _click("card-1", "Draft", sender_object_id="someone-else")
    assert stranger.type == "application/vnd.microsoft.activity.message"
    assert "who you are" in stranger.value
    platform.dispatch.assert_not_awaited()

    answered = await _click("card-1", "Draft")
    assert answered.type == "application/vnd.microsoft.card.adaptive"
    platform.dispatch.assert_awaited_once()

    again = await _click("card-1", "Ready for review")
    assert again.value == runs.ALREADY_ANSWERED
    platform.dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_teams_reply_offers_its_options_as_answer_buttons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, Any]] = []

    class _Bot:
        async def send(self, conversation: object, text: str, *, card: object = None) -> None:
            sent.append({"text": text, "card": card})

    conversation = TeamsConversationRef(
        service_url=_TEAMS_SERVICE_URL, conversation_id=_DIRECT_MESSAGE, bot_id="28:bot"
    )
    monkeypatch.setattr(
        reply,
        "get_config",
        lambda: {"configurable": {"thread_id": "t1", "teams_conversation": conversation.dump()}},
    )
    monkeypatch.setattr(reply.TeamsBot, "configured", staticmethod(lambda: _Bot()))
    long_option = "x" * 100

    result = await reply.teams_reply(
        "Draft or ready?",
        "final",
        options=["Draft", " ", "Ready for review", long_option, "4", "5", "6"],
    )

    assert result["success"] is True
    [posted] = sent
    assert posted["text"] == "Draft or ready?"
    buttons = posted["card"]["actions"]
    assert [button["data"]["answer"] for button in buttons] == [
        "Draft",
        "Ready for review",
        long_option,
        "4",
        "5",
    ]
    assert len(buttons[2]["title"]) == 75
    assert len({button["data"]["card"] for button in buttons}) == 1
