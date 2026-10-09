"""Teams deliveries reach the bot only with a verified token naming a Microsoft host."""

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from langgraph_runtime_inmem.queue import _enable_blockbuster
from microsoft_agents.authentication.msal import msal_auth
from microsoft_agents.hosting.core import ClaimsIdentity, JwtTokenValidator, TurnContext
from microsoft_agents.hosting.fastapi import CloudAdapter

from openswe.teams import bot
from openswe.teams.routes import router

_CLIENT_ID = "6a1f0c52-3c39-4a39-9a8e-2c5a3a0f4b11"
_TENANT_ID = "0b9f1e2d-7c6a-4f5e-8d3c-2b1a09f8e7d6"
_TEAMS_SERVICE_URL = "https://smba.trafficmanager.net/amer/"


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


def _activity(service_url: str) -> bytes:
    return json.dumps(
        {
            "type": "message",
            "id": "1",
            "channelId": "msteams",
            "serviceUrl": service_url,
            "from": {"id": "29:someone"},
            "recipient": {"id": f"28:{_CLIENT_ID}"},
            "conversation": {"id": "a:conversation", "tenantId": _TENANT_ID},
            "text": "hello",
        }
    ).encode()


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
    response = await _post(_activity(_TEAMS_SERVICE_URL), authorization)
    assert response.status_code == 401
    process_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_teams_webhook_never_replies_to_a_non_microsoft_service_url(
    process_activity: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    service_url = "https://attacker.example/"
    _verified_as_microsoft(monkeypatch, service_url)

    response = await _post(_activity(service_url), "Bearer signed-by-microsoft")

    assert response.status_code == 401
    assert "host validator" in response.text
    process_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_teams_webhook_replies_without_blocking_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies: list[object] = []

    async def send_activity(_context: TurnContext, activity: object, *_: object) -> None:
        replies.append(activity)

    _verified_as_microsoft(monkeypatch, _TEAMS_SERVICE_URL)
    monkeypatch.setattr(msal_auth, "ConfidentialClientApplication", _BlockingMsalClient)
    monkeypatch.setattr(TurnContext, "send_activity", send_activity)

    with langgraph_dev_blocking_checks():
        response = await _post(_activity(_TEAMS_SERVICE_URL), "Bearer signed-by-microsoft")

    assert response.status_code == 202
    assert replies == ["hi"]
