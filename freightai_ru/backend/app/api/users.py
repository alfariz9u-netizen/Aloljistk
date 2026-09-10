"""
User registration/role endpoints. Called only by the bot process (see
verify_bot_secret). Admin bootstrap: the FIRST time a user whose
telegram_id matches TELEGRAM_ADMIN_CHAT_ID calls upsert, they're promoted
to ADMIN automatically and permanently in the database -- after that,
admin-ness lives in users.role, not in the env var, and no endpoint here
lets a user set their own role to ADMIN.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import is_bootstrap_admin_telegram_id, verify_bot_secret
from app.models.models import User, UserRole
from app.schemas.schemas import SetRoleIn, UpsertUserIn
from app.services import db_utils, quotas, referrals

router = APIRouter(prefix="/internal/users", tags=["users"], dependencies=[Depends(verify_bot_secret)])


@router.post("/upsert")
async def upsert_user(payload: UpsertUserIn, db: AsyncSession = Depends(get_db)):
    stmt = select(User).where(User.telegram_id == payload.telegram_id)

    def make_row():
        role = UserRole.ADMIN if is_bootstrap_admin_telegram_id(payload.telegram_id) else UserRole.UNSET
        return User(telegram_id=payload.telegram_id, name=payload.name, phone=payload.phone, role=role)

    user, created = await db_utils.get_or_create(db, stmt, make_row)

    if created and payload.referral_code:
        await referrals.set_referred_by(db, user, payload.referral_code)

    if not created:
        user.last_seen_at = datetime.now(timezone.utc)
        if payload.name:
            user.name = payload.name

    had_phone_before = (not created) and user.phone is not None
    if payload.phone:
        user.phone = payload.phone
        if not had_phone_before:
            await referrals.reward_referral_if_needed(db, user)

    await db.commit()
    return {"id": str(user.id), "telegram_id": user.telegram_id, "role": user.role.value, "created": created}


@router.post("/role")
async def set_role(payload: SetRoleIn, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.telegram_id == payload.telegram_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == UserRole.ADMIN:
        raise HTTPException(status_code=400, detail="Admin role is managed separately")
    user.role = UserRole[payload.role]

    if user.role == UserRole.CARRIER:
        # Free launch trial -- see services/quotas.grant_carrier_trial_if_needed
        # for the full rationale and the one-time-per-user guard.
        quotas.grant_carrier_trial_if_needed(user)

    await db.commit()
    return {"id": str(user.id), "role": user.role.value}


@router.get("/{telegram_id}")
async def get_user(telegram_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "id": str(user.id), "telegram_id": user.telegram_id, "role": user.role.value, "name": user.name,
        "referral_points": user.referral_points,
        "daily_broadcast_used": user.daily_broadcast_used,
        "daily_quota_limit": quotas.daily_quota_limit(user),
        "subscription_active": user.subscription_active,
        "subscription_expires_at": user.subscription_expires_at.isoformat() if user.subscription_expires_at else None,
    }
