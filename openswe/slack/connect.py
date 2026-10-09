"""Sign in with Slack endpoints that link a Slack identity to a GitHub login."""

from fastapi import HTTPException

from openswe.dashboard.identity_links import IdentityProvider, LinkedAccount
from openswe.slack.oauth import (
    build_authorize_url,
    exchange_slack_code,
    fetch_slack_identity,
    slack_base_url,
    slack_oauth_configured,
    verify_team,
)


class SlackSignIn(IdentityProvider):
    """Sign in with Slack (OpenID Connect) on the app that owns the bot token.

    The member id and email come from Slack's verified OIDC claims, so a user
    can only ever link their own Slack account — no self-asserted values.
    """

    name = "slack"
    label = "Slack"

    def configured(self) -> bool:
        return slack_oauth_configured()

    def redirect_uri(self) -> str:
        return f"{slack_base_url()}/dashboard/api/slack/callback"

    def authorize_url(self, *, redirect_uri: str, state: str, nonce: str) -> str:
        return build_authorize_url(redirect_uri=redirect_uri, state=state)

    async def verified_account(self, code: str, *, redirect_uri: str, nonce: str) -> LinkedAccount:
        identity = await fetch_slack_identity(await exchange_slack_code(code, redirect_uri))
        verify_team(identity)
        if not identity.email or not identity.email_verified:
            raise HTTPException(400, "your Slack account has no verified email to link")
        return LinkedAccount(
            external_id=identity.user_id, email=identity.email, team_id=identity.team_id
        )


SLACK_SIGN_IN = SlackSignIn()
router = SLACK_SIGN_IN.router()
