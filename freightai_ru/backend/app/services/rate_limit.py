"""
Redis-backed sliding-window-ish rate limiting, keyed by the authenticated
user's telegram_id (never a client-supplied header, so it can't be
spoofed to bypass a limit). Separate scopes per project requirements:
messages/minute, loads/hour, trucks/hour, AI-extraction/hour. Values are
tunable via environment variables, see core/config.py.
"""
from datetime import datetime, timezone

from fastapi import HTTPException

from app.core.config import settings
from app.core.redis_client import get_redis

_SCOPES = {
    "messages": (settings.rate_limit_messages_per_minute, 60),
    "loads": (settings.rate_limit_loads_per_hour, 3600),
    "trucks": (settings.rate_limit_trucks_per_hour, 3600),
    "ai_extract": (settings.rate_limit_ai_per_hour, 3600),
    "admin_broadcast": (100, 3600),
}


async def check_and_increment(telegram_id: str, scope: str) -> dict:
    limit, window_seconds = _SCOPES.get(scope, (settings.rate_limit_messages_per_minute, 60))
    bucket = int(datetime.now(timezone.utc).timestamp() // window_seconds)
    key = f"ratelimit:{scope}:{telegram_id}:{bucket}"

    r = get_redis()
    current = await r.incr(key)
    if current == 1:
        await r.expire(key, window_seconds)

    if current > limit:
        raise HTTPException(
            status_code=429,
            detail=f"Вы превысили допустимый лимит ({limit}) для этого типа запросов. Попробуйте позже.",
        )
    return {"scope": scope, "used": current, "limit": limit}
