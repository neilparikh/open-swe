"""Microsoft Teams webhook HTTP routes."""

import logging

from fastapi import APIRouter, HTTPException, Request, Response

from openswe.teams.bot import TeamsBot

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/webhooks/teams")
async def teams_webhook(request: Request) -> Response:
    """Handle Bot Framework activities that Microsoft Teams delivers to the bot."""
    bot = TeamsBot.configured()
    if bot is None:
        logger.warning("Microsoft Teams is not configured — rejecting webhook request")
        raise HTTPException(status_code=503, detail="Microsoft Teams is not configured")
    return await bot.handle(request)


@router.get("/webhooks/teams")
async def teams_webhook_verify() -> dict[str, str]:
    """Health check for the Microsoft Teams messaging endpoint."""
    return {"status": "ok", "message": "Microsoft Teams webhook endpoint is active"}
