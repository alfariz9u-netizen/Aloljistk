"""
Carrier growth/priority system: a daily allowance of Broadcast
notifications (see services/notifications.py's broadcast_load_to_carrier)
plus a priority ranking for the ORDER carriers are notified in.

Deliberately scoped to Broadcast only, never to direct/proactive
matches -- a load that has a real, immediate match should always reach
that carrier regardless of quota or tier. Quota/priority only affects
"here's a new load looking for any available carrier" style
notifications, where there's genuine choice among several eligible
carriers, so tiering here doesn't cost a shipper their best match --
it only orders/limits who hears about it as advance opportunity.

Tiers, highest priority first:
  1. Paid subscriber (User.subscription_active) -- unlimited daily
     quota, always notified first.
  2. Referral-earning carriers -- daily quota bonus of
     REFERRAL_BONUS_PER_POINT per confirmed referral (see
     services/referrals.py), ranked among themselves by referral_points
     descending.
  3. Everyone else -- DAILY_BASE_QUOTA per day, ranked by registration
     order (oldest first, simple fair tie-break).
"""
from datetime import datetime, timedelta, timezone

from app.models.models import User

DAILY_BASE_QUOTA = 1
REFERRAL_BONUS_PER_POINT = 5


def _today():
    return datetime.now(timezone.utc).date()


def _has_active_subscription(user: User) -> bool:
    if not user.subscription_active:
        return False
    if user.subscription_expires_at is None:
        return True
    return user.subscription_expires_at > datetime.now(timezone.utc)


def _reset_if_new_day(user: User) -> None:
    today = _today()
    if user.daily_broadcast_reset_date != today:
        user.daily_broadcast_used = 0
        user.daily_broadcast_reset_date = today


def daily_quota_limit(user: User) -> int:
    return DAILY_BASE_QUOTA + REFERRAL_BONUS_PER_POINT * user.referral_points


def has_broadcast_quota(user: User) -> bool:
    _reset_if_new_day(user)
    if _has_active_subscription(user):
        return True
    return user.daily_broadcast_used < daily_quota_limit(user)


def consume_broadcast_quota(user: User) -> bool:
    _reset_if_new_day(user)
    if _has_active_subscription(user):
        return True
    if user.daily_broadcast_used >= daily_quota_limit(user):
        return False
    user.daily_broadcast_used += 1
    return True


def carrier_priority_sort_key(user: User) -> tuple:
    subscriber_rank = 0 if _has_active_subscription(user) else 1
    return (subscriber_rank, -user.referral_points, user.created_at)


def is_premium_tier(user: User) -> bool:
    return _has_active_subscription(user) or user.referral_points > 0


def grant_carrier_trial_if_needed(user: User, trial_days: int = 30) -> bool:
    """
    Free launch trial: the first time a user becomes a CARRIER, they get
    `trial_days` of subscription (unlimited quota + top priority tier)
    starting from THEIR OWN registration moment. Strictly one-time per
    user (guarded by user.trial_granted).
    """
    if user.trial_granted or trial_days <= 0:
        return False
    user.subscription_active = True
    user.subscription_expires_at = datetime.now(timezone.utc) + timedelta(days=trial_days)
    user.trial_granted = True
    return True
