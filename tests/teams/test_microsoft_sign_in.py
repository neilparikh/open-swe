"""Sign in with Microsoft links only an account Entra vouched for, in the bot's own tenant."""

import time
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from openswe.dashboard.identity_links import LinkedAccount
from openswe.teams import entra
from openswe.teams.connect import MICROSOFT_SIGN_IN
from openswe.teams.entra import EntraApp

_PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_CLIENT_ID = "6a1f0c52-3c39-4a39-9a8e-2c5a3a0f4b11"
_TENANT_ID = "0b9f1e2d-7c6a-4f5e-8d3c-2b1a09f8e7d6"
_NONCE = "this-flows-nonce"


class _SigningKey:
    def __init__(self) -> None:
        self.key = _PRIVATE.public_key()


class _Keys:
    def get_signing_key_from_jwt(self, token: str) -> _SigningKey:
        return _SigningKey()


@pytest.fixture(autouse=True)
def entra_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEAMS_CLIENT_ID", _CLIENT_ID)
    monkeypatch.setenv("TEAMS_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("TEAMS_TENANT_ID", _TENANT_ID)
    monkeypatch.setattr(entra, "_keys", lambda jwks_url: _Keys())


def _id_token(**overrides: object) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": f"https://login.microsoftonline.com/{_TENANT_ID}/v2.0",
        "aud": _CLIENT_ID,
        "oid": "alice-object-id",
        "tid": _TENANT_ID,
        "nonce": _NONCE,
        "preferred_username": "alice@contoso.example",
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, _PRIVATE, "RS256")


async def _sign_in(monkeypatch: pytest.MonkeyPatch, id_token: str) -> LinkedAccount:
    monkeypatch.setattr(EntraApp, "_id_token", AsyncMock(return_value=id_token))
    return await MICROSOFT_SIGN_IN.verified_account(
        "code", redirect_uri="http://localhost:2024/dashboard/api/microsoft/callback", nonce=_NONCE
    )


@pytest.mark.asyncio
async def test_sign_in_links_the_entra_object_id_teams_sends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = await _sign_in(monkeypatch, _id_token())
    assert account == LinkedAccount(
        external_id="alice-object-id", login="alice@contoso.example", team_id=_TENANT_ID
    )


_ANOTHER_TENANT = "1c2d3e4f-0000-4000-8000-000000000000"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"tid": _ANOTHER_TENANT}, id="another-tenant"),
        pytest.param(
            {"iss": f"https://login.microsoftonline.com/{_ANOTHER_TENANT}/v2.0"},
            id="another-issuer",
        ),
        pytest.param({"aud": "another-app"}, id="another-app"),
        pytest.param({"nonce": "another-flows-nonce"}, id="another-sign-in"),
        pytest.param({"iat": int(time.time()) - 900, "exp": int(time.time()) - 600}, id="expired"),
        pytest.param({"oid": None}, id="no-object-id"),
    ],
)
async def test_sign_in_rejects_a_token_entra_did_not_issue_for_this_flow(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object]
) -> None:
    with pytest.raises(HTTPException) as rejected:
        await _sign_in(monkeypatch, _id_token(**overrides))
    assert rejected.value.status_code == 400
