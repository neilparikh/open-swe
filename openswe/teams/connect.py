"""Sign in with Microsoft, linking the Entra account Teams knows a person by."""

import logging

from fastapi import HTTPException

from openswe.dashboard.identity_links import IdentityProvider, LinkedAccount
from openswe.teams.entra import EntraApp, SignInRejected
from openswe.utils.dashboard_links import dashboard_api_base_url

logger = logging.getLogger(__name__)


class MicrosoftSignIn(IdentityProvider):
    """Sign in with the bot's own Entra app, so only its tenant's people can link.

    Teams names a message's sender by Entra object id, the ``oid`` of the id
    token verified here, so the link rests on nothing the sender asserts. The
    email claim is optional and unverified in Entra, so none is stored.
    """

    name = "microsoft"
    label = "Microsoft"

    def configured(self) -> bool:
        return EntraApp.configured() is not None

    def redirect_uri(self) -> str:
        return f"{dashboard_api_base_url()}/dashboard/api/microsoft/callback"

    def authorize_url(self, *, redirect_uri: str, state: str, nonce: str) -> str:
        return _app().authorize_url(redirect_uri=redirect_uri, state=state, nonce=nonce)

    async def verified_account(self, code: str, *, redirect_uri: str, nonce: str) -> LinkedAccount:
        try:
            account = await _app().signed_in_account(code, redirect_uri=redirect_uri, nonce=nonce)
        except SignInRejected as exc:
            logger.warning("Rejected a Microsoft sign in", extra={"sign_in_error": str(exc)})
            raise HTTPException(
                400, "Microsoft could not verify that sign in — please retry"
            ) from None
        return LinkedAccount(
            external_id=account.oid, login=account.preferred_username, team_id=account.tid
        )


def _app() -> EntraApp:
    app = EntraApp.configured()
    if app is None:
        raise HTTPException(500, "Sign in with Microsoft is not configured")
    return app


MICROSOFT_SIGN_IN = MicrosoftSignIn()
router = MICROSOFT_SIGN_IN.router()
