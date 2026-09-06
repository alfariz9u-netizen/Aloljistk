"""
Referral capture + reward. A user's referred_by_user_id is set ONCE, at
the moment their row is first created (see api/users.py's upsert,
called from the /start deep-link handler in bot/handlers/start.py) --
never editable afterwards, and a user can never set it to their own id
(self-referral is rejected, see set_referred_by below).

The reward (referrer gets +1 referral_points, see services/quotas.py)
only fires when the REFERRED user completes real registration --
specifically, the first time they submit a phone number (see
api/users.py's upsert, called from bot's got_phone step) -- not merely
at /start. This is a deliberate anti-abuse gate: rewarding on /start
alone would let someone farm referral_points by spamming fake /start
deep-link clicks with no real registrations behind them.
"""
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import User

logger = logging.getLogger("freightai")


async def set_referred_by(db: AsyncSession, new_user: User, referrer_telegram_id: str) -> None:
    """Called only for a BRAND NEW user row (never overwrites an
    existing referred_by_user_id -- see the `if new_user.referred_by_user_id
    is None` guard at the call site in api/users.py)."""
    if referrer_telegram_id == new_user.telegram_id:
        logger.warning("ignored self-referral attempt: telegram_id=%s", new_user.telegram_id)
        return
    result = await db.execute(select(User).where(User.telegram_id == referrer_telegram_id))
    referrer = result.scalar_one_or_none()
    if referrer is None:
        logger.info("referral code did not match any user: %s", referrer_telegram_id)
        return
    new_user.referred_by_user_id = referrer.id


async def reward_referral_if_needed(db: AsyncSession, user: User) -> None:
    """
    Call this when a user completes registration (first phone number
    submission). If they were referred and haven't already triggered the
    reward, credits the referrer +1 referral_points and flips
    referral_rewarded so this can never fire twice for the same user
    (idempotent by design, not just by accident).
    """
    if user.referred_by_user_id is None or user.referral_rewarded:
        return
    referrer = await db.get(User, user.referred_by_user_id)
    if referrer is None:
        # Referrer account was somehow removed since -- nothing to credit.
        user.referral_rewarded = True
        return
    referrer.referral_points += 1
    user.referral_rewarded = True
    logger.info("referral rewarded: referrer=%s new_referral_points=%s", referrer.telegram_id, referrer.referral_points)
