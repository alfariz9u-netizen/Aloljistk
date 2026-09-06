"""
Premium auto-connect tests (dormant unless settings.auto_connect_premium_enabled
is True). Behavior under test:
  - disabled (default): a fresh, never-trusted match always goes through
    normal admin-mediated MATCH_FOUND, regardless of carrier tier
  - enabled: a match with a premium-tier carrier (subscriber OR has
    referral_points) skips admin mediation and auto-connects, exactly
    like an established TrustedPair
  - enabled but carrier is NOT premium: falls back to normal
    admin-mediated flow
  - proactive/backhaul matches are NEVER auto-connected by this
    setting, regardless of carrier tier (only notify_proactive_match's
    own logic governs those)
"""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core.config import settings
from app.models.models import Load, LoadStatus, MatchStatus, Truck, User, UserRole
from app.services.matching import MatchResult, create_match_safely
from app.services.notifications import process_new_match, should_skip_admin_mediation


async def _make_match(db_session, shipper_tid: str, carrier_tid: str, **carrier_kwargs):
    shipper = User(telegram_id=shipper_tid, role=UserRole.SHIPPER, name="Shipper")
    carrier = User(telegram_id=carrier_tid, role=UserRole.CARRIER, name="Carrier", phone="0700000000", **carrier_kwargs)
    db_session.add_all([shipper, carrier])
    await db_session.flush()

    load = Load(user_id=shipper.id, origin_city="Бишкек", destination_city="Ош", status=LoadStatus.NEW)
    truck = Truck(user_id=carrier.id, current_city="Бишкек", desired_destination="Ош")
    db_session.add_all([load, truck])
    await db_session.flush()

    match, _ = await create_match_safely(db_session, load, truck, MatchResult(score=90, reasons=["test"]))
    return load, truck, match


@pytest.mark.asyncio
async def test_disabled_by_default_even_for_premium_carrier(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auto_connect_premium_enabled", False)
    load, truck, match = await _make_match(db_session, "premium_shipper_1", "premium_carrier_1", subscription_active=True)
    assert await should_skip_admin_mediation(db_session, load, truck) is False


@pytest.mark.asyncio
async def test_enabled_and_subscriber_skips_admin(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auto_connect_premium_enabled", True)
    load, truck, match = await _make_match(db_session, "premium_shipper_2", "premium_carrier_2", subscription_active=True)
    assert await should_skip_admin_mediation(db_session, load, truck) is True


@pytest.mark.asyncio
async def test_enabled_and_referral_holder_skips_admin(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auto_connect_premium_enabled", True)
    load, truck, match = await _make_match(db_session, "premium_shipper_3", "premium_carrier_3", referral_points=1)
    assert await should_skip_admin_mediation(db_session, load, truck) is True


@pytest.mark.asyncio
async def test_enabled_but_carrier_not_premium_still_goes_through_admin(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auto_connect_premium_enabled", True)
    load, truck, match = await _make_match(db_session, "premium_shipper_4", "premium_carrier_4")
    assert await should_skip_admin_mediation(db_session, load, truck) is False


@pytest.mark.asyncio
async def test_process_new_match_auto_connects_premium_carrier_end_to_end(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auto_connect_premium_enabled", True)
    load, truck, match = await _make_match(db_session, "premium_shipper_5", "premium_carrier_5", subscription_active=True)

    with patch("app.services.telegram_client.send_message", new=AsyncMock(return_value=True)):
        await process_new_match(db_session, load, truck, match, admin_id=None)
    await db_session.commit()

    assert match.status == MatchStatus.CONNECTED
    assert load.status == LoadStatus.CONNECTED


@pytest.mark.asyncio
async def test_process_new_match_stays_admin_mediated_for_non_premium(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auto_connect_premium_enabled", True)
    load, truck, match = await _make_match(db_session, "premium_shipper_6", "premium_carrier_6")

    with patch("app.services.telegram_client.send_message", new=AsyncMock(return_value=True)):
        await process_new_match(db_session, load, truck, match, admin_id=None)
    await db_session.commit()

    assert match.status == MatchStatus.PENDING  # normal flow, admin still must act
    assert load.status != LoadStatus.CONNECTED
