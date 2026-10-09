"""The Microsoft Entra app registration behind the Teams bot.

One registration serves both directions: Bot Framework authenticates the bot
with it, and people sign in with it to link the Entra account Teams knows them
by. It is single-tenant, so only people in its tenant can do either.
"""

import asyncio
import hmac
from dataclasses import dataclass, field
from functools import cache
from typing import Self
from urllib.parse import urlencode

import httpx2
import jwt
from jwt import PyJWKClient
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openswe.config import ENV
from openswe.utils.http import DEFAULT_HTTP_TIMEOUT

_SIGN_IN_SCOPE = "openid profile"
_ALGORITHMS = ["RS256"]
_REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "oid", "tid", "nonce"]
_KEY_CACHE_SECONDS = 900


class SignInRejected(Exception):
    """Entra did not vouch for the account a sign in claims."""


class SignedInAccount(BaseModel):
    """Who an Entra id token says signed in."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    oid: str = Field(min_length=1)
    tid: str = Field(min_length=1)
    preferred_username: str = ""


@dataclass(frozen=True)
class EntraApp:
    """The registration's credentials, and sign in as its OpenID Connect client."""

    client_id: str
    tenant_id: str
    client_secret: str = field(repr=False)

    @classmethod
    def configured(cls) -> Self | None:
        """The app the ``TEAMS_*`` settings name, or ``None`` when Teams is not set up."""
        client_id = ENV.TEAMS_CLIENT_ID.get()
        client_secret = ENV.TEAMS_CLIENT_SECRET.get()
        tenant_id = ENV.TEAMS_TENANT_ID.get()
        if not (client_id and client_secret and tenant_id):
            return None
        return cls(client_id=client_id, tenant_id=tenant_id, client_secret=client_secret)

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    def authorize_url(self, *, redirect_uri: str, state: str, nonce: str) -> str:
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "response_mode": "query",
            "redirect_uri": redirect_uri,
            "scope": _SIGN_IN_SCOPE,
            "prompt": "select_account",
            "state": state,
            "nonce": nonce,
        }
        return f"{self.authority}/oauth2/v2.0/authorize?{urlencode(params)}"

    async def signed_in_account(
        self, code: str, *, redirect_uri: str, nonce: str
    ) -> SignedInAccount:
        """Redeem an authorization code and verify the id token that comes back.

        Raises :class:`SignInRejected` unless the token is signed by Entra, was
        issued to this app in its tenant, and carries the flow's ``nonce``.
        """
        id_token = await self._id_token(code, redirect_uri)
        try:
            # PyJWT fetches Entra's signing keys with blocking I/O.
            return await asyncio.to_thread(self._verify, id_token, nonce)
        except (jwt.PyJWTError, ValidationError) as exc:
            raise SignInRejected(str(exc)) from exc

    async def _id_token(self, code: str, redirect_uri: str) -> str:
        async with httpx2.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
            response = await client.post(
                f"{self.authority}/oauth2/v2.0/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "scope": _SIGN_IN_SCOPE,
                },
            )
        try:
            body = response.json()
        except ValueError:
            body = None
        id_token = body.get("id_token") if isinstance(body, dict) else None
        if not isinstance(id_token, str) or not id_token:
            error = body.get("error") if isinstance(body, dict) else None
            raise SignInRejected(
                f"token endpoint returned no id token ({error or response.status_code})"
            )
        return id_token

    def _verify(self, id_token: str, nonce: str) -> SignedInAccount:
        key = _keys(f"{self.authority}/discovery/v2.0/keys").get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            key.key,
            algorithms=_ALGORITHMS,
            audience=self.client_id,
            issuer=f"{self.authority}/v2.0",
            options={"require": _REQUIRED_CLAIMS},
        )
        if claims["tid"] != self.tenant_id:
            raise jwt.InvalidTokenError("id token is from another tenant")
        if not hmac.compare_digest(str(claims["nonce"]), nonce):
            raise jwt.InvalidTokenError("id token was issued to another sign in")
        return SignedInAccount.model_validate(claims)


@cache
def _keys(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url, cache_keys=True, lifespan=_KEY_CACHE_SECONDS)
