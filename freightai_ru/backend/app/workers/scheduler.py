"""
Background worker process (runs separately from the API/bot, see
docker-compose.yml's `worker` service). Two recurring jobs:

  1. Reminders: broadcast notifications older than REMINDER_DELAY_MINUTES
     that are still SENT (not INTERESTED/REJECTED/matched) get one
     reminder push.
  2. Proactive/backhaul matching: scans ON_TRIP trucks near their ETA.

If more than one worker instance runs (horizontal scaling), a Postgres
advisory lock ensures each job body only executes in one process at a
time per tick -- the others just skip that tick rather than double-run.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.core.database import async_session
from app.core.config import settings
from app.models.models import Load, LoadStatus, Notification, NotificationStatus, NotificationType, User
from app.services import notifications
from app.services.proactive import run_proactive_scan

logger = logging.getLogger("freightai")

# Arbitrary but fixed advisory-lock keys, one per job, so the two jobs
# never block each other -- only concurrent runs of the SAME job do.
_LOCK_REMINDERS = 911001
_LOCK_PROACTIVE = 911002


async def _with_advisory_lock(db, key: int, coro):
    got = (await db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})).scalar_one()
    if not got:
        return None
    try:
        return await coro
    finally:
        await db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})


async def run_reminder_pass() -> int:
    async with async_session() as db:
        async def _job():
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.reminder_delay_minutes)
            result = await db.execute(
                select(Notification).where(
                    Notification.notification_type == NotificationType.BROADCAST,
                    Notification.status == NotificationStatus.SENT,
                    Notification.reminder_sent.is_(False),
                    Notification.sent_at <= cutoff,
                )
            )
            candidates = result.scalars().all()
            sent = 0
            for notif in candidates:
                load = await db.get(Load, notif.load_id)
                if load is None or load.status != LoadStatus.WAITING_FOR_MATCH:
                    continue  # already matched/cancelled -- do not remind
                carrier = await db.get(User, notif.user_id)
                if carrier is None:
                    continue
                await notifications.send_reminder(db, load, carrier, notif)
                sent += 1
            await db.commit()
            return sent

        result = await _with_advisory_lock(db, _LOCK_REMINDERS, _job())
        return result or 0


async def run_proactive_pass() -> int:
    async with async_session() as db:
        async def _job():
            return await run_proactive_scan(db)
        result = await _with_advisory_lock(db, _LOCK_PROACTIVE, _job())
        return result or 0


async def main_loop(poll_seconds: int = 60):
    logger.info("worker started, polling every %ss", poll_seconds)
    while True:
        try:
            reminded = await run_reminder_pass()
            matched = await run_proactive_pass()
            if reminded or matched:
                logger.info("worker tick: reminders=%s proactive_matches=%s", reminded, matched)
        except Exception:
            logger.exception("worker tick failed -- will retry next interval")
        await asyncio.sleep(poll_seconds)


if __name__ == "__main__":
    asyncio.run(main_loop())
