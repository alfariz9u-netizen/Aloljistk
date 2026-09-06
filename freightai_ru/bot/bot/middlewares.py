"""
Bot-side guardrails that must hold BEFORE anything reaches the backend:
- message length cap (cost/DoS safety, independent of backend AI limits)
- a simple per-user messages/minute throttle, using Redis directly here
  since this is UX-level throttling, not a business rate limit (those
  live server-side in the backend, keyed by telegram_id, and can't be
  bypassed by talking to the backend directly since it requires
  X-Bot-Secret -- this middleware is just to keep the bot itself snappy
  under spam).
"""
import logging
import time
from typing import Any, Awaitable, Callable

import redis.asyncio as aioredis
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from bot.config import settings

logger = logging.getLogger("freightai.bot")
_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


class GuardrailsMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            if event.text and len(event.text) > settings.max_free_text_length:
                await event.answer(
                    f"Сообщение слишком длинное (максимум {settings.max_free_text_length} символов). "
                    "Пожалуйста, сократите запрос."
                )
                return None

            user_id = str(event.from_user.id) if event.from_user else "unknown"
            try:
                r = _get_redis()
                bucket = int(time.time() // 60)
                key = f"bot:msgrate:{user_id}:{bucket}"
                current = await r.incr(key)
                if current == 1:
                    await r.expire(key, 60)
                if current > settings.rate_limit_messages_per_minute:
                    await event.answer("Вы отправили слишком много сообщений за минуту. Пожалуйста, подождите немного.")
                    return None
            except Exception:
                logger.exception("rate limit check failed -- failing open")

        return await handler(event, data)
