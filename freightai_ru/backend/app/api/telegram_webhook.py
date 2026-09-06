"""
POST /telegram/webhook -- only mounted when BOT_MODE=webhook (see
app/main.py). Telegram calls this URL directly for every update instead
of the bot process long-polling for them, which lets the whole app
(API + bot) run as a single HTTP service with no standalone background
process -- required for free-tier hosts that only run request-driven
web services.

Verified via the X-Telegram-Bot-Api-Secret-Token header, which Telegram
echoes back on every webhook call because we register it as
`secret_token` in setWebhook on startup (see app/main.py). A request
without the correct header is rejected before the update ever reaches
the dispatcher -- the URL alone is guessable, the secret is not.
"""
import logging

from aiogram.types import Update
from fastapi import APIRouter, Header, HTTPException, Request

from app.core.config import settings

logger = logging.getLogger("freightai")

router = APIRouter(tags=["telegram-webhook"])


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str = Header(default="")):
    if settings.bot_mode != "webhook":
        # Route always exists so app.include_router() can run
        # unconditionally at import time (simpler than mutating the
        # router during the startup event) -- but it's inert unless
        # BOT_MODE=webhook is actually configured.
        raise HTTPException(status_code=404, detail="Not found")

    if not settings.telegram_webhook_secret or x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    bot = getattr(request.app.state, "bot", None)
    dp = getattr(request.app.state, "dp", None)
    if bot is None or dp is None:
        # Shouldn't happen if startup wired things correctly, but never
        # let a misconfiguration 500 loudly to Telegram's retry logic.
        logger.error("telegram_webhook called but bot/dispatcher not initialized")
        raise HTTPException(status_code=503, detail="Bot not ready")

    try:
        payload = await request.json()
        update = Update.model_validate(payload)
        await dp.feed_update(bot, update)
    except Exception:
        logger.exception("Failed to process incoming Telegram update")
        # Still return 200 -- returning an error here makes Telegram
        # retry the same update repeatedly, which isn't what we want
        # for a bug on our side. The failure is already logged above.
    return {"ok": True}
