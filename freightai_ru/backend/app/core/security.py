"""
Server-side authorization helpers.

Admin status is NEVER derived from a Telegram username and NEVER settable
by a user through the bot or API. It comes from either:
  1. users.role == ADMIN in the database (set only by the one-time
     scripts/promote_admin.py operator script), or
  2. the request's telegram_id matching TELEGRAM_ADMIN_CHAT_ID on the very
     first bootstrap (handled in api/users.py's upsert, once, when no
     admin exists yet).

Every mutating/reading endpoint additionally requires the shared
BOT_SERVICE_SECRET header, proving the caller is our own bot process and
not an arbitrary client hitting the API directly.
"""
from fastapi import Header, HTTPException

from app.core.config import settings


def verify_bot_secret(x_bot_secret: str = Header(default="")) -> None:
    if not settings.bot_service_secret or x_bot_secret != settings.bot_service_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")


def is_bootstrap_admin_telegram_id(telegram_id: str) -> bool:
    return bool(settings.telegram_admin_chat_id) and str(telegram_id) == str(settings.telegram_admin_chat_id)
