"""
Payment gating tests (Phase 2 monetization, dormant by default).

Behavior under test:
  - when payments are disabled (Phase 1 default), contact is always
    unlocked regardless of any Payment row
  - when enabled, a fresh match's contact is LOCKED until a Payment for
    it is CONFIRMED
  - a TrustedPair is always exempt, even with payments enabled
  - get_or_create_pending_payment is idempotent (one row per match)
  - confirm_payment_manual is idempotent (double-confirm is a no-op,
    not an error)
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core.config import settings
from app.models.models import Load, LoadStatus, Match, MatchStatus, PaymentStatus, Truck, User, UserRole
from app.services.matching import MatchResult, create_match_safely
from app.services.payments import (
    confirm_payment_manual, get_or_create_pending_payment, get_payment_for_match, is_contact_unlocked,
)
from app.services.trusted_pairs import mark_trusted


async def _make_match(db_session, shipper_tid: str, carrier_tid: str):
    shipper = User(telegram_id=shipper_tid, role=UserRole.SHIPPER, name="Shipper")
    carrier = User(telegram_id=carrier_tid, role=UserRole.CARRIER, name="Carrier", phone="0700000000")
    db_session.add_all([shipper, carrier])
    await db_session.flush()

    load = Load(user_id=shipper.id, origin_city="Бишкек", destination_city="Ош", status=LoadStatus.NEW)
    truck = Truck(user_id=carrier.id, current_city="Бишкек", desired_destination="Ош")
    db_session.add_all([load, truck])
    await db_session.flush()

    match, _ = await create_match_safely(db_session, load, truck, MatchResult(score=90, reasons=["test"]))
    return load, truck, match


@pytest.mark.asyncio
async def test_contact_always_unlocked_when_payments_disabled(db_session, monkeypatch):
    monkeypatch.setattr(settings, "payments_enabled", False)
    load, truck, match = await _make_match(db_session, "pay_shipper_1", "pay_carrier_1")
    assert await is_contact_unlocked(db_session, load, truck, match) is True


@pytest.mark.asyncio
async def test_contact_locked_until_payment_confirmed(db_session, monkeypatch):
    monkeypatch.setattr(settings, "payments_enabled", True)
    load, truck, match = await _make_match(db_session, "pay_shipper_2", "pay_carrier_2")

    assert await is_contact_unlocked(db_session, load, truck, match) is False

    payment = await get_or_create_pending_payment(db_session, load, truck, match)
    assert payment.status == PaymentStatus.PENDING
    assert await is_contact_unlocked(db_session, load, truck, match) is False  # still pending

    await confirm_payment_manual(db_session, payment, admin_id=None)
    await db_session.commit()
    assert payment.status == PaymentStatus.CONFIRMED
    assert await is_contact_unlocked(db_session, load, truck, match) is True


@pytest.mark.asyncio
async def test_trusted_pair_always_exempt_even_with_payments_enabled(db_session, monkeypatch):
    monkeypatch.setattr(settings, "payments_enabled", True)
    load, truck, match = await _make_match(db_session, "pay_shipper_3", "pay_carrier_3")

    await mark_trusted(db_session, load.user_id, truck.user_id)
    await db_session.commit()

    # No Payment row was ever created, yet contact is unlocked because
    # this pair already paid once, under admin supervision, before.
    assert await is_contact_unlocked(db_session, load, truck, match) is True


@pytest.mark.asyncio
async def test_get_or_create_pending_payment_is_idempotent(db_session, monkeypatch):
    monkeypatch.setattr(settings, "payments_enabled", True)
    load, truck, match = await _make_match(db_session, "pay_shipper_4", "pay_carrier_4")

    first = await get_or_create_pending_payment(db_session, load, truck, match)
    await db_session.commit()
    second = await get_or_create_pending_payment(db_session, load, truck, match)

    assert first.id == second.id
    stored = await get_payment_for_match(db_session, match.id)
    assert stored.id == first.id


@pytest.mark.asyncio
async def test_confirm_payment_manual_is_idempotent(db_session, monkeypatch):
    monkeypatch.setattr(settings, "payments_enabled", True)
    load, truck, match = await _make_match(db_session, "pay_shipper_5", "pay_carrier_5")

    payment = await get_or_create_pending_payment(db_session, load, truck, match)
    await confirm_payment_manual(db_session, payment, admin_id=None)
    first_confirmed_at = payment.confirmed_at

    # Confirming again must not error or change the confirmation time.
    await confirm_payment_manual(db_session, payment, admin_id=None)
    assert payment.confirmed_at == first_confirmed_at


@pytest.mark.asyncio
async def test_default_payer_is_carrier(db_session, monkeypatch):
    monkeypatch.setattr(settings, "payments_enabled", True)
    monkeypatch.setattr(settings, "payment_charge_party", "carrier")
    load, truck, match = await _make_match(db_session, "pay_shipper_6", "pay_carrier_6")

    payment = await get_or_create_pending_payment(db_session, load, truck, match)
    assert payment.payer_user_id == truck.user_id


@pytest.mark.asyncio
async def test_charge_party_shipper_config(db_session, monkeypatch):
    monkeypatch.setattr(settings, "payments_enabled", True)
    monkeypatch.setattr(settings, "payment_charge_party", "shipper")
    load, truck, match = await _make_match(db_session, "pay_shipper_7", "pay_carrier_7")

    payment = await get_or_create_pending_payment(db_session, load, truck, match)
    assert payment.payer_user_id == load.user_id
