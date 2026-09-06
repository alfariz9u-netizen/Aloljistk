"""
Bot entrypoint / builder.

Two ways this module gets used:
  1. Standalone process (docker-compose's `bot` service, for a VPS/
     Oracle Cloud-style always-on deployment): `python -m bot.main`
     runs `main()` below, which builds the bot + dispatcher and starts
     long polling. Unchanged behavior from before.
  2. Imported by the BACKEND (`app.main`) when BOT_MODE=webhook, for
     single-service free-tier hosting: the backend calls
     `build_bot()` / `build_dispatcher()` directly and feeds updates
     into the dispatcher itself from a `/telegram/webhook` route,
     instead of this module ever calling `start_polling`. Same
     handlers, same logic, either way -- only the transport differs.

Never crashes the whole process on a single update's exception --
aiogram already isolates handler exceptions per update, and we
additionally log everything so nothing fails silently.
"""
import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage

from bot.config import settings
from bot.handlers import admin, intake, start
from bot.middlewares import GuardrailsMiddleware

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)r}',
    stream=sys.stdout,
)
logger = logging.getLogger("freightai.bot")


def build_bot() -> Bot:
    return Bot(token=settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def build_dispatcher() -> Dispatcher:
    storage = RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(storage=storage)
    dp.message.middleware(GuardrailsMiddleware())
    dp.include_router(start.router)
    dp.include_router(admin.router)
    dp.include_router(intake.router)
    return dp


async def main():
    """Standalone long-polling entrypoint -- used only when the bot runs
    as its own process (BOT_MODE=polling). Not used in webhook mode."""
    if not settings.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN is not set -- bot cannot start.")
        sys.exit(1)

    bot = build_bot()
    dp = build_dispatcher()

    logger.info("bot starting (long polling)")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
