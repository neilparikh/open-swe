"""Linking an account on another platform to the person signed in to the dashboard.

Every "Sign in with ..." connection follows one flow: the dashboard starts it,
the provider vouches for an account, and that account is attached to whoever
holds the session that started it — never to anyone the provider's reply names.
The desktop app's flow ends in the system browser, which holds neither the
app's session nor the state cookie, so there the verified account travels back
through a PKCE handoff and is linked under the session the app itself holds.
"""

import hmac
import logging
from abc import ABC, abstractmethod
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.oauth import (
    STATE_TTL_SECONDS,
    DesktopConnectExchange,
    cookie_security,
    decode_state,
    desktop_callback_url,
    desktop_handoff_from_state,
    frontend_base_url,
    hash_state_nonce,
    issue_connect_handoff,
    issue_state,
    new_state_nonce,
    redeem_connect_handoff,
    require_session,
    sanitize_redirect_to,
    session_user_id,
    valid_handoff_challenge,
)
from openswe.users import Provider, User

logger = logging.getLogger(__name__)


class LinkedAccount(BaseModel):
    """An account a provider vouched for, ready to attach to a person.

    It also rides in the desktop handoff code, which a browser records, so it
    names only what was connected, never whose account to connect it to.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    external_id: str = Field(min_length=1)
    login: str = ""
    email: str = ""
    team_id: str = ""


class IdentityProvider(ABC):
    """A platform whose accounts people link to the one they use Open SWE with."""

    name: Provider
    label: str

    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    def redirect_uri(self) -> str:
        """Where the provider sends the browser back; registered with the provider."""

    @abstractmethod
    def authorize_url(self, *, redirect_uri: str, state: str, nonce: str) -> str:
        """The provider's consent page. ``nonce`` is unique to this flow."""

    @abstractmethod
    async def verified_account(self, code: str, *, redirect_uri: str, nonce: str) -> LinkedAccount:
        """The account behind an authorization code, as the provider vouches for it."""

    def router(self) -> APIRouter:
        router = APIRouter(tags=[self.name])
        router.add_api_route(
            f"/{self.name}/login",
            self._login,
            methods=["GET"],
            name=f"{self.name}_login",
            description=f"Start Sign in with {self.label} to link it to the signed-in person.",
        )
        router.add_api_route(
            f"/{self.name}/callback",
            self._callback,
            methods=["GET"],
            name=f"{self.name}_callback",
            description=f"Link the {self.label} account the provider verified.",
        )
        router.add_api_route(
            f"/{self.name}/desktop/exchange",
            self._desktop_exchange,
            methods=["POST"],
            name=f"{self.name}_desktop_exchange",
            description=f"Finish a desktop {self.label} link with the app's own session.",
        )
        router.add_api_route(
            f"/{self.name}/link",
            self._disconnect,
            methods=["DELETE"],
            name=f"{self.name}_disconnect",
            description=f"Unlink the signed-in person's {self.label} account.",
        )
        return router

    @property
    def _state_cookie(self) -> str:
        return f"osw_{self.name}_oauth_state"

    @property
    def _cookie_path(self) -> str:
        return f"/dashboard/api/{self.name}"

    async def _login(
        self,
        desktop_handoff: str | None = None,
        desktop_port: int | None = Query(default=None, ge=1024, le=65535),
        _session: dict[str, Any] = SESSION_DEP,
    ) -> RedirectResponse:
        if not self.configured():
            raise HTTPException(500, f"Sign in with {self.label} is not configured")
        nonce = new_state_nonce()
        nonce_hash = hash_state_nonce(nonce)
        state = issue_state(
            redirect_to=f"{frontend_base_url()}/my-settings/connections",
            nonce_hash=nonce_hash,
            handoff_challenge=valid_handoff_challenge(desktop_handoff),
            handoff_port=desktop_port,
        )
        response = RedirectResponse(
            self.authorize_url(redirect_uri=self.redirect_uri(), state=state, nonce=nonce_hash),
            status_code=302,
        )
        secure, _ = cookie_security()
        response.set_cookie(
            key=self._state_cookie,
            value=nonce,
            max_age=STATE_TTL_SECONDS,
            httponly=True,
            secure=secure,
            samesite="lax",
            path=self._cookie_path,
        )
        return response

    async def _callback(self, request: Request, code: str, state: str) -> RedirectResponse:
        state_payload = decode_state(state)
        nonce_hash = state_payload.get("nonce_hash")
        if not isinstance(nonce_hash, str):
            raise HTTPException(400, "oauth state mismatch — please retry")

        handoff = desktop_handoff_from_state(state_payload)
        if handoff is not None:
            challenge, port = handoff
            account = await self._verified_account(code, nonce_hash)
            handoff_code = issue_connect_handoff(
                provider=self.name, challenge=challenge, claims=account.model_dump()
            )
            response = RedirectResponse(desktop_callback_url(port, handoff_code), status_code=302)
            self._clear_state_cookie(response)
            return response

        session = require_session(request)
        cookie_nonce = request.cookies.get(self._state_cookie)
        if not cookie_nonce or not hmac.compare_digest(hash_state_nonce(cookie_nonce), nonce_hash):
            raise HTTPException(400, "oauth state mismatch — please retry")
        await self._link(session, await self._verified_account(code, nonce_hash))

        redirect_to = sanitize_redirect_to(state_payload.get("redirect_to")) or frontend_base_url()
        response = RedirectResponse(redirect_to, status_code=302)
        self._clear_state_cookie(response)
        return response

    async def _desktop_exchange(
        self,
        body: DesktopConnectExchange,
        session: dict[str, Any] = SESSION_DEP,
    ) -> dict[str, bool]:
        claims = redeem_connect_handoff(provider=self.name, code=body.code, verifier=body.verifier)
        try:
            account = LinkedAccount.model_validate(claims)
        except ValidationError:
            raise HTTPException(400, "malformed handoff code") from None
        await self._link(session, account)
        return {"connected": True}

    @audit_endpoint
    async def _disconnect(self, session: dict[str, Any] = SESSION_DEP) -> dict[str, bool]:
        user = await self._session_user(session)
        if user is not None:
            await user.unlink(self.name)
        return {"connected": False}

    async def _verified_account(self, code: str, nonce: str) -> LinkedAccount:
        return await self.verified_account(code, redirect_uri=self.redirect_uri(), nonce=nonce)

    async def _link(self, session: dict[str, Any], account: LinkedAccount) -> None:
        """Attach ``account`` to the person the session belongs to, if they have a users row."""
        user = await self._session_user(session)
        if user is None:
            logger.info(
                "Account not linked: no user for this GitHub login",
                extra={"github_login": session["sub"], "linked_provider": self.name},
            )
            return
        await user.link(
            self.name,
            account.external_id,
            login=account.login,
            email=account.email,
            team_id=account.team_id,
        )

    @staticmethod
    async def _session_user(session: dict[str, Any]) -> User | None:
        """The person the session belongs to.

        Sessions minted before the ``user_id`` claim existed fall back to the
        GitHub login.
        """
        user_id = session_user_id(session)
        if user_id is not None:
            return await User.get(user_id)
        return await User.for_login("github", session["sub"])

    def _clear_state_cookie(self, response: Response) -> None:
        secure, _ = cookie_security()
        response.delete_cookie(
            self._state_cookie, path=self._cookie_path, samesite="lax", secure=secure
        )
