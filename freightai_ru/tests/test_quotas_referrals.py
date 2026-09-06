"""
Tests for the carrier growth/priority system (services/quotas.py,
services/referrals.py).
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.models.models import User, UserRole
from app.services.quotas import (
    carrier_priority_sort_key, consume_broadcast_quota, daily_quota_limit, has_broadcast_quota,
)
from app.services.referrals import reward_referral_if_needed, set_referred_by


def _make_carrier(**kwargs) -> User:
    defaults = dict(telegram_id="quota_test", role=UserRole.CARRIER)
    defaults.update(kwargs)
    return User(**defaults)


def test_base_daily_quota_is_one_with_no_referrals():
    user = _make_carrier()
    assert daily_quota_limit(user) == 1


def test_referral_points_add_five_per_point():
    user = _make_carrier(referral_points=3)
    assert daily_quota_limit(user) == 1 + 5 * 3


def test_quota_consumed_until_exhausted_then_blocks():
    user = _make_carrier()  # base quota = 1
    assert consume_broadcast_quota(user) is True   # 1st use: ok
    assert consume_broadcast_quota(user) is False  # 2nd use same day: blocked
    assert has_broadcast_quota(user) is False


def test_quota_resets_on_a_new_day():
    user = _make_carrier()
    consume_broadcast_quota(user)
    assert has_broadcast_quota(user) is False

    # Simulate the reset date being "yesterday" -- the next check should
    # reset the counter rather than staying blocked forever.
    user.daily_broadcast_reset_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    assert has_broadcast_quota(user) is True


def test_active_subscriber_has_unlimited_quota():
    user = _make_carrier(subscription_active=True)
    for _ in range(50):
        assert consume_broadcast_quota(user) is True
    assert user.daily_broadcast_used == 0  # subscribers never consume a counted unit


def test_expired_subscription_falls_back_to_normal_quota():
    user = _make_carrier(
        subscription_active=True,
        subscription_expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    assert consume_broadcast_quota(user) is True   # base quota still applies
    assert consume_broadcast_quota(user) is False  # and still exhausts normally


def test_priority_sort_subscriber_first():
    subscriber = _make_carrier(telegram_id="sub", subscription_active=True, created_at=datetime.now(timezone.utc))
    regular = _make_carrier(telegram_id="reg", created_at=datetime.now(timezone.utc))
    ranked = sorted([regular, subscriber], key=carrier_priority_sort_key)
    assert ranked[0] is subscriber


def test_priority_sort_by_referral_points_when_no_subscription():
    low = _make_carrier(telegram_id="low", referral_points=1, created_at=datetime.now(timezone.utc))
    high = _make_carrier(telegram_id="high", referral_points=10, created_at=datetime.now(timezone.utc))
    ranked = sorted([low, high], key=carrier_priority_sort_key)
    assert ranked[0] is high


@pytest.mark.asyncio
async def test_referral_rejects_self_referral(db_session):
    user = User(telegram_id="self_ref_1", role=UserRole.CARRIER)
    db_session.add(user)
    await db_session.flush()

    await set_referred_by(db_session, user, "self_ref_1")
    assert user.referred_by_user_id is None


@pytest.mark.asyncio
async def test_referral_reward_increments_referrer_points_once(db_session):
    referrer = User(telegram_id="referrer_1", role=UserRole.CARRIER)
    referred = User(telegram_id="referred_1", role=UserRole.CARRIER)
    db_session.add_all([referrer, referred])
    await db_session.flush()

    await set_referred_by(db_session, referred, "referrer_1")
    await db_session.commit()
    assert referred.referred_by_user_id == referrer.id

    await reward_referral_if_needed(db_session, referred)
    await db_session.commit()
    assert referrer.referral_points == 1
    assert referred.referral_rewarded is True

    # Calling again (e.g. a second phone-number submission) must not
    # double-reward the same referral.
    await reward_referral_if_needed(db_session, referred)
    await db_session.commit()
    assert referrer.referral_points == 1


@pytest.mark.asyncio
async def test_unreferred_user_reward_is_a_no_op(db_session):
    user = User(telegram_id="no_referrer_1", role=UserRole.CARRIER)
    db_session.add(user)
    await db_session.flush()

    await reward_referral_if_needed(db_session, user)  # should not raise
    assert user.referral_rewarded is False
