"""
Proactive / backhaul matching: scans trucks currently ON_TRIP and looks
for WAITING_FOR_MATCH loads whose origin equals the truck's trip
destination, within an ETA window (default ±60 min, see
PROACTIVE_MATCH_WINDOW_MINUTES). Deliberately coarse -- this is a
"might be a fit, admin confirms" signal, not a precise scheduling system.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.models.models import Load, LoadStatus, Match, Truck, TruckStatus, User, UserRole
from app.services import notifications
from app.services.cities import normalize_city
from app.services.matching import MatchResult, create_match_safely

logger = logging.getLogger("freightai")


async def run_proactive_scan(db) -> int:
    """Returns the number of new proactive matches created. Safe to call
    from multiple workers concurrently: match creation is race-safe
    (unique constraint), and each truck is marked proactive_scan_done
    after a successful pass within a transaction, avoiding endlessly
    re-notifying the same truck for the same window."""
    now = datetime.now(timezone.utc)
    window = timedelta(minutes=settings.proactive_match_window_minutes)

    result = await db.execute(
        select(Truck).where(
            Truck.is_deleted.is_(False),
            Truck.status == TruckStatus.ON_TRIP,
            Truck.trip_eta.isnot(None),
            Truck.trip_eta <= now + window,
            Truck.trip_eta >= now - window,
        )
    )
    trucks = result.scalars().all()
    if not trucks:
        return 0

    admin_result = await db.execute(select(User).where(User.role == UserRole.ADMIN).limit(1))
    admin = admin_result.scalar_one_or_none()

    created_count = 0
    for truck in trucks:
        dest = normalize_city(truck.trip_destination)
        if not dest:
            continue
        loads_result = await db.execute(
            select(Load).where(
                Load.is_deleted.is_(False),
                Load.status == LoadStatus.WAITING_FOR_MATCH,
            )
        )
        candidate_loads = [l for l in loads_result.scalars().all() if normalize_city(l.origin_city) == dest]

        for load in candidate_loads:
            result = MatchResult(score=70, reasons=[
                "↩ Возможность обратного груза (Backhaul)",
                f"Ожидаемое прибытие в течение ±{settings.proactive_match_window_minutes} минут",
            ])
            match, created = await create_match_safely(db, load, truck, result, is_proactive=True)
            if created:
                created_count += 1
                await notifications.notify_proactive_match(db, load, truck, match, admin.id if admin else None)

        truck.proactive_scan_done = True

    await db.commit()
    return created_count
