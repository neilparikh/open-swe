"""The Microsoft Teams bot, built on the Microsoft 365 Agents SDK.

It verifies each Bot Framework delivery, hands direct messages to
:mod:`openswe.teams.runs`, logs installs, and posts the agent's replies back into
a conversation after the request that started the run has ended.
"""

import asyncio
import logging
from functools import lru_cache
from http import HTTPStatus

import jwt
from fastapi import HTTPException, Request, Response
from microsoft_agents.activity import (
    Activity,
    ActivityTypes,
    ChannelAccount,
    ConversationAccount,
    ConversationReference,
    TextFormatTypes,
)
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

from openswe.source_context import TeamsConversationRef
from openswe.teams.entra import EntraApp
from openswe.teams.runs import handle_message
from openswe.utils.http import bearer_token

logger = logging.getLogger(__name__)


class TeamsDeliveryRefused(Exception):
    """A reply would go somewhere other than Microsoft's Bot Framework hosts."""


class TeamsBot:
    """The Bot Framework identity this deployment answers Microsoft Teams as."""

    def __init__(self, app: EntraApp) -> None:
        # Issuer checks are opt-in in the SDK; without them any Entra-signed
        # token that names our client id as its audience would pass.
        config = AgentAuthConfiguration(
            client_id=app.client_id,
            client_secret=app.client_secret,
            tenant_id=app.tenant_id,
            validate_issuer=True,
        )
        # Registering the config here is also what tells the validator which
        # audiences to accept.
        connections = ConnectionManager(
            provider_factory=_NonBlockingMsalAuth,
            connections_configurations={"SERVICE_CONNECTION": config},
        )
        self._client_id = app.client_id
        self._validator = JwtTokenValidator(config)
        # Off by default. On, replies only go to a Microsoft host that the signed
        # token itself named, so the bot's own token never leaves Microsoft.
        self._hosts = OutboundHostValidator(enabled=True)
        self._adapter = CloudAdapter(connection_manager=connections, host_validator=self._hosts)
        self._app = AgentApplication[TurnState](
            ApplicationOptions(storage=MemoryStorage(), start_typing_timer=False),
            connection_manager=connections,
        )
        self._app.activity("message")(handle_message)
        self._app.activity("installationUpdate")(_log_installation)

    @staticmethod
    def configured() -> TeamsBot | None:
        """The bot for the current ``TEAMS_*`` settings, or ``None`` when Teams is not set up."""
        app = EntraApp.configured()
        return None if app is None else _bot(app)

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

    async def send(self, conversation: TeamsConversationRef, text: str) -> None:
        """Post markdown ``text`` into a Teams conversation outside any inbound request.

        The SDK's proactive path skips the host check its inbound path makes, and
        a stored reference must never route the bot's token anywhere but Microsoft.
        """
        service_url = conversation.service_url
        if not service_url.startswith("https://") or not self._hosts.is_allowed(service_url):
            raise TeamsDeliveryRefused(f"{service_url!r} is not a Bot Framework service URL")
        if not conversation.conversation_id or not conversation.bot_id:
            raise TeamsDeliveryRefused("the conversation reference names no conversation or bot")
        reference = ConversationReference(
            channel_id="msteams",
            service_url=service_url,
            conversation=ConversationAccount(
                id=conversation.conversation_id, tenant_id=conversation.tenant_id or None
            ),
            agent=ChannelAccount(id=conversation.bot_id),
            user=(
                ChannelAccount(
                    id=conversation.user_id,
                    aad_object_id=conversation.user_aad_object_id or None,
                )
                if conversation.user_id
                else None
            ),
        )

        async def post(context: TurnContext) -> None:
            await context.send_activity(
                Activity(
                    type=ActivityTypes.message, text=text, text_format=TextFormatTypes.markdown
                )
            )

        await self._adapter.continue_conversation(
            self._client_id, reference.get_continuation_activity(), post
        )


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
def _bot(app: EntraApp) -> TeamsBot:
    # Keyed by the settings, so a rotated secret builds a fresh bot.
    return TeamsBot(app)


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
