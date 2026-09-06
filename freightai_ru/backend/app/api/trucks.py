"""
Truck registration + trip updates. Ownership is always derived from the
authenticated telegram_id in the payload (resolved to a User row here),
never trusted as a bare truck_id/user_id from the client for WRITE
operations.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import verify_bot_secret
from app.models.models import (
    Load, LoadStatus, Truck, TruckStatus, User, UserRole,
)
from app.schemas.schemas import TruckCreateIn
from app.services import notifications
from app.services.audit import log_action
from app.services.matching import create_match_safely, score_match

router = APIRouter(prefix="/internal/trucks", tags=["trucks"], dependencies=[Depends(verify_bot_secret)])


async def _get_admin(db: AsyncSession):
    result = await db.execute(select(User).where(User.role == UserRole.ADMIN).limit(1))
    return result.scalar_one_or_none()


@router.post("")
async def create_truck(payload: TruckCreateIn, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.telegram_id == payload.telegram_id))
    user = result.scalar_one_or_none()
    if user is None or user.role != UserRole.CARRIER:
        raise HTTPException(status_code=403, detail="Вы должны быть зарегистрированы как перевозчик")

    truck = Truck(
        user_id=user.id,
        truck_type=payload.truck_type,
        current_city=payload.current_city,
        desired_destination=payload.desired_destination,
        available=payload.available,
        status=TruckStatus.AVAILABLE,
    )
    if payload.has_current_trip:
        truck.status = TruckStatus.ON_TRIP
        truck.trip_origin = payload.trip_origin
        truck.trip_destination = payload.trip_destination
        if payload.trip_eta_minutes_from_now is not None:
            truck.trip_eta = datetime.now(timezone.utc) + timedelta(minutes=payload.trip_eta_minutes_from_now)

    db.add(truck)
    await db.flush()
    await log_action(db, user.id, "truck_created", "truck", truck.id)
    await db.commit()
    await db.refresh(truck)

    # Scenario 1: a truck registers -- look for a waiting load it can serve now.
    match_created = None
    if truck.status == TruckStatus.AVAILABLE and truck.available:
        result = await db.execute(
            select(Load).where(Load.is_deleted.is_(False), Load.status == LoadStatus.WAITING_FOR_MATCH)
        )
        waiting_loads = result.scalars().all()
        best = None
        for load in waiting_loads:
            score = score_match(load, truck)
            if score is not None and (best is None or score.score > best[1].score):
                best = (load, score)
        if best:
            load, score = best
            admin = await _get_admin(db)
            match, created = await create_match_safely(db, load, truck, score)
            if created:
                await db.commit()
                # See services/trusted_pairs.py -- auto-connects if the
                # admin already vetted this exact shipper/carrier pair.
                await notifications.process_new_match(db, load, truck, match, admin.id if admin else None)
                await db.commit()
                match_created = str(match.id)

    return {"id": str(truck.id), "status": truck.status.value, "match_created": match_created}


@router.get("/{telegram_id}")
async def list_my_trucks(telegram_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    trucks_result = await db.execute(
        select(Truck).where(Truck.user_id == user.id, Truck.is_deleted.is_(False))
    )
    trucks = trucks_result.scalars().all()
    return [
        {"id": str(t.id), "current_city": t.current_city, "status": t.status.value, "available": t.available}
        for t in trucks
    ]
