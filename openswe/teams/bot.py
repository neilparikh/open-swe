"""The Microsoft Teams bot, built on the Microsoft 365 Agents SDK.

For now it only exercises installation and Bot Framework auth: it verifies each
delivery, answers every message with "hi", and logs installs and removals.
"""

import asyncio
import logging
from functools import lru_cache
from http import HTTPStatus

import jwt
from fastapi import HTTPException, Request, Response
from microsoft_agents.authentication.msal import MsalAuth
from microsoft_agents.hosting.core import (
    AgentApplication,
    AgentAuthConfiguration,
    ApplicationOptions,
    ConnectionManager,
    JwtTokenValidator,
    MemoryStorage,
    OutboundHostValidator,
    TurnContext,
    TurnState,
)
from microsoft_agents.hosting.fastapi import CloudAdapter

from openswe.config import ENV
from openswe.utils.http import bearer_token

logger = logging.getLogger(__name__)


class TeamsBot:
    """The Bot Framework identity this deployment answers Microsoft Teams as."""

    def __init__(self, *, client_id: str, client_secret: str, tenant_id: str) -> None:
        # Issuer checks are opt-in in the SDK; without them any Entra-signed
        # token that names our client id as its audience would pass.
        config = AgentAuthConfiguration(
            client_id=client_id,
            client_secret=client_secret,
            tenant_id=tenant_id,
            validate_issuer=True,
        )
        # Registering the config here is also what tells the validator which
        # audiences to accept.
        connections = ConnectionManager(
            provider_factory=_NonBlockingMsalAuth,
            connections_configurations={"SERVICE_CONNECTION": config},
        )
        self._validator = JwtTokenValidator(config)
        # Off by default. On, replies only go to a Microsoft host that the signed
        # token itself named, so the bot's own token never leaves Microsoft.
        self._adapter = CloudAdapter(
            connection_manager=connections, host_validator=OutboundHostValidator(enabled=True)
        )
        self._app = AgentApplication[TurnState](
            ApplicationOptions(storage=MemoryStorage(), start_typing_timer=False),
            connection_manager=connections,
        )
        self._app.activity("message")(_say_hi)
        self._app.activity("installationUpdate")(_log_installation)

    @staticmethod
    def configured() -> TeamsBot | None:
        """The bot for the current ``TEAMS_*`` settings, or ``None`` when Teams is not set up."""
        client_id = ENV.TEAMS_CLIENT_ID.get()
        client_secret = ENV.TEAMS_CLIENT_SECRET.get()
        tenant_id = ENV.TEAMS_TENANT_ID.get()
        if not (client_id and client_secret and tenant_id):
            return None
        return _bot(client_id, client_secret, tenant_id)

    async def handle(self, request: Request) -> Response:
        """Answer one Bot Framework delivery once its sender is verified."""
        token = bearer_token(request)
        if not token:
            raise HTTPException(status_code=401, detail="Invalid token")
        try:
            identity = await self._validator.validate_token(token)
        except (jwt.PyJWTError, ValueError) as exc:
            logger.warning("Rejected Teams bot token", extra={"teams_error": str(exc)})
            raise HTTPException(status_code=401, detail="Invalid token") from None
        # The adapter treats a request without claims as anonymous, so it must
        # only ever see requests verified above.
        request.state.claims_identity = identity
        response = await self._adapter.process(request, self._app)
        return response or Response(status_code=HTTPStatus.ACCEPTED)


class _NonBlockingMsalAuth(MsalAuth):
    """``MsalAuth`` that builds its MSAL client off the event loop.

    The SDK builds the client inside this async call the first time it needs a
    token, and MSAL's constructor fetches the tenant's OpenID metadata with a
    blocking HTTP request. Once built, the client is cached and token requests
    already run in a thread.
    """

    async def get_access_token(
        self, resource_url: str, scopes: list[str], force_refresh: bool = False
    ) -> str:
        await asyncio.to_thread(self._get_client)
        return await super().get_access_token(resource_url, scopes, force_refresh)


@lru_cache(maxsize=1)
def _bot(client_id: str, client_secret: str, tenant_id: str) -> TeamsBot:
    # Keyed by the settings, so a rotated secret builds a fresh bot.
    return TeamsBot(client_id=client_id, client_secret=client_secret, tenant_id=tenant_id)


async def _say_hi(context: TurnContext, _state: TurnState) -> None:
    await context.send_activity("hi")


async def _log_installation(context: TurnContext, _state: TurnState) -> None:
    conversation = context.activity.conversation
    logger.info(
        "Teams app installation changed",
        extra={
            "teams_action": context.activity.action,
            "teams_tenant_id": conversation.tenant_id if conversation else None,
            "teams_conversation_type": conversation.conversation_type if conversation else None,
        },
    )
