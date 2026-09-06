"""
Trusted pairs: once the admin manually connects a shipper and a carrier
for the first time (the "✅ Связано" action, see api/admin.py), any
FUTURE match between that same shipper and that same carrier is
auto-connected -- see services/notifications.process_new_match(). This
does not weaken the privacy model: the two parties already exchanged
contact info once, under admin supervision, so showing it to them again
on a repeat match isn't a new exposure. It only removes the friction of
making the admin manually re-approve a relationship they already vetted.

A pair is NEVER created automatically from matching/scoring, and never
creatable by either party -- only mark_trusted(), called exclusively
from the admin "connect" action, writes one.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import TrustedPair


async def is_trusted_pair(db: AsyncSession, shipper_user_id: uuid.UUID, carrier_user_id: uuid.UUID) -> bool:
    result = await db.execute(
        select(TrustedPair).where(
            TrustedPair.shipper_user_id == shipper_user_id,
            TrustedPair.carrier_user_id == carrier_user_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def mark_trusted(db: AsyncSession, shipper_user_id: uuid.UUID, carrier_user_id: uuid.UUID) -> None:
    """Idempotent: safe to call every time the admin connects a match,
    even if this pair is already trusted."""
    pair = TrustedPair(shipper_user_id=shipper_user_id, carrier_user_id=carrier_user_id)
    try:
        async with db.begin_nested():
            db.add(pair)
            await db.flush()
    except IntegrityError:
        await db.rollback()  # already trusted -- no-op
