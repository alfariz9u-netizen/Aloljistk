"""
POST /internal/cron/tick -- runs one pass of the reminder + proactive-
matching jobs (see app/workers/scheduler.py) and returns immediately.

This exists so a single free-tier web service (no standalone worker
process allowed) can still get the "10-minute reminder" and "proactive/
backhaul matching" behavior: an external, free scheduled pinger (e.g.
cron-job.org) calls this endpoint every few minutes. Each call is a
plain HTTP request, so the same external ping that keeps the service
warm on a sleep-after-idle host is what actually drives these jobs --
no separate always-on worker needed.

Guarded by its own CRON_SECRET (not the bot's shared secret) -- this
caller only ever needs the ability to trigger this one narrow,
idempotent action, nothing else, so it gets its own least-privilege
credential. The jobs themselves are already safe to call repeatedly or
concurrently: services/notifications.py's unique constraints prevent
duplicate reminders, and the workers use Postgres advisory locks so an
overlapping call just skips rather than double-running.
"""
from fastapi import APIRouter, Header, HTTPException

from app.core.config import settings
from app.workers.scheduler import run_proactive_pass, run_reminder_pass

router = APIRouter(prefix="/internal/cron", tags=["cron"])


def _verify_cron_secret(x_cron_secret: str) -> None:
    if not settings.cron_secret or x_cron_secret != settings.cron_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/tick")
async def tick(x_cron_secret: str = Header(default="")):
    _verify_cron_secret(x_cron_secret)
    reminded = await run_reminder_pass()
    proactive_matches = await run_proactive_pass()
    return {"reminded": reminded, "proactive_matches": proactive_matches}
