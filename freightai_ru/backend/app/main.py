"""
FastAPI backend entrypoint. Only the bot process/webhook, the cron
pinger, and internal workers ever call this API -- see
core/security.verify_bot_secret (required on every /internal/* route
except /internal/cron/tick, which has its own CRON_SECRET) and
api/telegram_webhook.py (its own TELEGRAM_WEBHOOK_SECRET). No debug
info or stack traces are exposed to callers in production.

Two deployment shapes, both served by this same app (see
core/config.Settings.bot_mode):
  - BOT_MODE=polling: bot runs as its own separate process
    (docker-compose's `bot` service) long-polling Telegram -- this app
    only ever exposes the internal API. Suited to a VPS/always-on VM.
  - BOT_MODE=webhook: this app ALSO builds the Telegram bot on startup
    and exposes POST /telegram/webhook for it, and the standalone
    `worker` process is replaced by an external pinger hitting
    POST /internal/cron/tick every few minutes. Suited to a free,
    single-web-service host with no standalone background workers.
"""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import admin, cron, extract, health, interests, loads, telegram_webhook, trucks, users
from app.core.config import settings
from app.core.database import Base, engine
from app.core.logging_config import configure_logging
from app.models import models  # noqa: F401  -- registers all tables on Base.metadata

logger = configure_logging()

app = FastAPI(title="FreightAI MVP Backend", debug=(settings.environment != "production"))


@app.on_event("startup")
async def on_startup():
    # MVP convenience: create tables if they don't exist yet. A real
    # production rollout should switch to `alembic upgrade head` (the
    # scaffolding is already in place under backend/alembic/) instead of
    # relying on create_all, but this keeps both `docker compose up
    # --build` and a single-service free-host deploy working with zero
    # extra migration steps.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("database tables ensured")

    if settings.bot_mode == "webhook":
        # Deferred import: the `bot` package (and its aiogram/redis
        # deps) is only needed in this mode -- see the merged webapp
        # Dockerfile at the repo root, which is the only image that
        # bundles both `app/` and `bot/` together.
        from bot.main import build_bot, build_dispatcher

        bot = build_bot()
        dp = build_dispatcher()
        app.state.bot = bot
        app.state.dp = dp

        webhook_url = f"{settings.public_base_url.rstrip('/')}/telegram/webhook"
        await bot.set_webhook(
            url=webhook_url,
            secret_token=settings.telegram_webhook_secret,
            drop_pending_updates=True,
        )
        logger.info("telegram webhook registered at %s", webhook_url)


app.include_router(health.router)
app.include_router(users.router)
app.include_router(trucks.router)
app.include_router(loads.router)
app.include_router(interests.router)
app.include_router(admin.router)
app.include_router(extract.router)
app.include_router(cron.router)
app.include_router(telegram_webhook.router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    detail = str(exc) if settings.environment != "production" else "Произошла временная ошибка, попробуйте ещё раз чуть позже."
    return JSONResponse(status_code=500, content={"detail": detail})
