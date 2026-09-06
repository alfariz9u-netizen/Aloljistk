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
from datetime import datetime, timezone

from app.models.models import User

DAILY_BASE_QUOTA = 1
REFERRAL_BONUS_PER_POINT = 5


def _today():
    return datetime.now(timezone.utc).date()


def _has_active_subscription(user: User) -> bool:
    if not user.subscription_active:
        return False
    if user.subscription_expires_at is None:
        return True  # no expiry set -- treat as indefinite (e.g. admin-granted, no end date given)
    return user.subscription_expires_at > datetime.now(timezone.utc)


def _reset_if_new_day(user: User) -> None:
    """Lazy reset: rather than a scheduled job resetting every carrier's
    counter at midnight, each quota check just compares today's date
    against the last reset date and zeroes the counter if a new day has
    started. Cheap, and never drifts out of sync."""
    today = _today()
    if user.daily_broadcast_reset_date != today:
        user.daily_broadcast_used = 0
        user.daily_broadcast_reset_date = today


def daily_quota_limit(user: User) -> int:
    """Effectively infinite for active subscribers -- callers should
    check _has_active_subscription separately before relying on a
    numeric comparison against this, since "unlimited" isn't a number."""
    return DAILY_BASE_QUOTA + REFERRAL_BONUS_PER_POINT * user.referral_points


def has_broadcast_quota(user: User) -> bool:
    """Does NOT mutate/reset state -- call _reset_if_new_day first (done
    inside consume_broadcast_quota) if you need an up-to-date read."""
    _reset_if_new_day(user)
    if _has_active_subscription(user):
        return True
    return user.daily_broadcast_used < daily_quota_limit(user)


def consume_broadcast_quota(user: User) -> bool:
    """
    Checks quota and, if available, consumes one unit (increments
    daily_broadcast_used) in the same call -- callers should treat a
    True return as "already consumed, go ahead and send," not just "you
    may send." Subscribers consume nothing (unlimited).

    Returns False without mutating anything if quota is exhausted --
    the caller should skip sending this carrier a Broadcast for this
    load (they'll be eligible again next reset, or for the next load if
    they still have quota left).
    """
    _reset_if_new_day(user)
    if _has_active_subscription(user):
        return True
    if user.daily_broadcast_used >= daily_quota_limit(user):
        return False
    user.daily_broadcast_used += 1
    return True


def carrier_priority_sort_key(user: User) -> tuple:
    """
    Sort carriers with this as the key (ascending) to get subscribers
    first, then descending referral_points, then oldest-registered
    first as a fair tie-break. Used when building the eligible-carrier
    list for a new Broadcast (see api/loads.py) so higher-tier carriers
    are notified -- and consume their quota check -- before lower-tier
    ones, which matters when quota is scarce relative to how many
    eligible carriers exist.
    """
    subscriber_rank = 0 if _has_active_subscription(user) else 1
    return (subscriber_rank, -user.referral_points, user.created_at)


def is_premium_tier(user: User) -> bool:
    """
    A "premium" carrier: an active paid subscriber, OR someone with at
    least one successful referral. Used by
    services/notifications.should_skip_admin_mediation to decide
    whether a match involving this carrier can skip admin review
    entirely (see settings.auto_connect_premium_enabled -- dormant
    unless explicitly turned on, and the trade-off it makes: that
    carrier's matches lose the admin's fraud/quality screening in
    exchange for speed). Referral_points > 0 is enough on its own --
    unlike the daily-quota bonus, this perk doesn't scale with how many
    referrals someone has, just whether they've earned at least one.
    """
    return _has_active_subscription(user) or user.referral_points > 0
