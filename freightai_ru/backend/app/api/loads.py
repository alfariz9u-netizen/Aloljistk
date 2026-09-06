"""
Load registration + immediate matching + broadcast-when-no-match, per
project scenarios 1 and 2.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import verify_bot_secret
from app.models.models import Load, LoadStatus, Truck, TruckStatus, User, UserRole
from app.schemas.schemas import LoadCreateIn
from app.services import notifications, quotas
from app.services.audit import log_action
from app.services.matching import create_match_safely, find_best_match_for_load

router = APIRouter(prefix="/internal/loads", tags=["loads"], dependencies=[Depends(verify_bot_secret)])


async def _get_admin(db: AsyncSession):
    result = await db.execute(select(User).where(User.role == UserRole.ADMIN).limit(1))
    return result.scalar_one_or_none()


async def _eligible_carriers_for_broadcast(db: AsyncSession, load: Load) -> list[User]:
    """Filtering per project rule 10: never blind-broadcast to every
    user. Only carriers with a currently AVAILABLE truck whose type is
    unspecified or matches the load's requested truck type.

    Ordered by priority tier (see services/quotas.carrier_priority_sort_key)
    -- paid subscribers first, then by referral_points descending, then
    oldest-registered -- so higher-tier carriers are notified (and have
    their quota consumed) before lower-tier ones. This only affects
    ORDER and WHO gets skipped when quota runs out; it never limits a
    load's own chances of being seen -- see create_load below, which
    still attempts every eligible carrier in priority order until quota
    for that carrier is exhausted."""
    result = await db.execute(
        select(Truck).where(
            Truck.is_deleted.is_(False),
            Truck.available.is_(True),
            Truck.status == TruckStatus.AVAILABLE,
        )
    )
    trucks = result.scalars().all()
    eligible_user_ids: set = set()
    for t in trucks:
        if load.truck_type and t.truck_type and t.truck_type.strip().lower() != load.truck_type.strip().lower():
            continue
        eligible_user_ids.add(t.user_id)
    if not eligible_user_ids:
        return []
    users_result = await db.execute(select(User).where(User.id.in_(eligible_user_ids)))
    carriers = list(users_result.scalars().all())
    carriers.sort(key=quotas.carrier_priority_sort_key)
    return carriers


@router.post("")
async def create_load(payload: LoadCreateIn, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.telegram_id == payload.telegram_id))
    user = result.scalar_one_or_none()
    if user is None or user.role != UserRole.SHIPPER:
        raise HTTPException(status_code=403, detail="Вы должны быть зарегистрированы как грузовладелец")

    load = Load(
        user_id=user.id,
        origin_city=payload.origin_city,
        destination_city=payload.destination_city,
        truck_type=payload.truck_type,
        truck_count=payload.truck_count,
        loading_time=payload.loading_time,
        notes=payload.notes,
        raw_text=payload.raw_text,
        status=LoadStatus.NEW,
    )
    db.add(load)
    await db.flush()
    await log_action(db, user.id, "load_created", "load", load.id)
    await db.commit()
    await db.refresh(load)

    admin = await _get_admin(db)
    best = await find_best_match_for_load(db, load)
    if best:
        truck, score = best
        match, created = await create_match_safely(db, load, truck, score)
        await db.commit()
        if created:
            # process_new_match auto-connects (and reveals contact info)
            # if this shipper/carrier pair was already vetted by the
            # admin before; otherwise it's the normal admin-mediated
            # MATCH_FOUND flow. See services/trusted_pairs.py.
            await notifications.process_new_match(db, load, truck, match, admin.id if admin else None)
            await db.commit()
        return {"id": str(load.id), "status": load.status.value, "match_id": str(match.id)}

    load.status = LoadStatus.WAITING_FOR_MATCH
    await db.commit()
    await notifications.notify_no_match_yet(db, load)
    await db.commit()

    carriers = await _eligible_carriers_for_broadcast(db, load)
    notified = 0
    for carrier in carriers:
        # Quota check happens in priority order (subscribers, then
        # highest referral_points, ... -- see _eligible_carriers_for_broadcast's
        # sort). A carrier who's out of quota today simply doesn't get
        # THIS load's broadcast; they remain eligible for the next one
        # once their quota resets. Subscribers never fail this check
        # (unlimited, see services/quotas.py).
        if not quotas.has_broadcast_quota(carrier):
            continue
        notif = await notifications.broadcast_load_to_carrier(db, load, carrier)
        if notif is not None:
            # Only spend quota on a broadcast that actually sent --
            # never on a no-op (e.g. this carrier was somehow already
            # notified about this exact load before).
            quotas.consume_broadcast_quota(carrier)
            notified += 1
    await db.commit()

    return {"id": str(load.id), "status": load.status.value, "broadcast_to": notified}


@router.get("/{telegram_id}")
async def list_my_loads(telegram_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    loads_result = await db.execute(
        select(Load).where(Load.user_id == user.id, Load.is_deleted.is_(False))
    )
    loads = loads_result.scalars().all()
    return [{"id": str(l.id), "origin_city": l.origin_city, "destination_city": l.destination_city,
             "status": l.status.value} for l in loads]
