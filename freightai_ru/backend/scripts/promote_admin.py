"""
One-time operator script to promote a user to ADMIN, or to demote one.
Run inside the backend container:

    docker compose exec backend python -m scripts.promote_admin <telegram_id>

This is the ONLY supported way to grant ADMIN after the very first
bootstrap admin (see backend/app/core/security.is_bootstrap_admin_telegram_id
and api/users.py) -- there is deliberately no API endpoint that lets any
user, including an existing admin, self-promote through the bot or HTTP API.
"""
import asyncio
import sys

sys.path.insert(0, "/app")  # container layout: backend code lives at /app

from sqlalchemy import select  # noqa: E402

from app.core.database import async_session  # noqa: E402
from app.models.models import User, UserRole  # noqa: E402


async def promote(telegram_id: str):
    async with async_session() as db:
        result = await db.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if user is None:
            print(f"No user found with telegram_id={telegram_id}. They must /start the bot first.")
            return
        user.role = UserRole.ADMIN
        await db.commit()
        print(f"User {telegram_id} ({user.name}) is now ADMIN.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m scripts.promote_admin <telegram_id>")
        sys.exit(1)
    asyncio.run(promote(sys.argv[1]))
