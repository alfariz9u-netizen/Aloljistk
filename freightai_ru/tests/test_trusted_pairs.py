"""
Trusted-pair auto-connect tests.

Behavior under test: a match between a shipper and carrier who have
NEVER been connected by the admin before goes through the normal
admin-mediated MATCH_FOUND flow (PENDING status, no contact info
revealed). Once the admin has connected them once (mark_trusted), any
FUTURE match between that exact same pair is auto-connected immediately
(CONNECTED status) with contact info included in the notification --
because they already have each other's info from the first connection,
so this isn't a new exposure.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.models.models import Load, LoadStatus, MatchStatus, Truck, User, UserRole
from app.services.matching import MatchResult, create_match_safely
from app.services.notifications import process_new_match
from app.services.trusted_pairs import is_trusted_pair, mark_trusted


@pytest.mark.asyncio
async def test_first_match_is_not_auto_connected(db_session):
    shipper = User(telegram_id="tp_shipper_1", role=UserRole.SHIPPER, name="Shipper A", phone="0500000001")
    carrier = User(telegram_id="tp_carrier_1", role=UserRole.CARRIER, name="Carrier A", phone="0500000002")
    db_session.add_all([shipper, carrier])
    await db_session.flush()

    load = Load(user_id=shipper.id, origin_city="Бишкек", destination_city="Ош", status=LoadStatus.NEW)
    truck = Truck(user_id=carrier.id, current_city="Бишкек", desired_destination="Ош")
    db_session.add_all([load, truck])
    await db_session.flush()

    match, created = await create_match_safely(db_session, load, truck, MatchResult(score=90, reasons=["test"]))
    assert created

    with patch("app.services.telegram_client.send_message", new=AsyncMock(return_value=True)):
        await process_new_match(db_session, load, truck, match, admin_id=None)
    await db_session.commit()

    assert match.status == MatchStatus.PENDING  # normal flow, not auto-connected
    assert load.status != LoadStatus.CONNECTED


@pytest.mark.asyncio
async def test_second_match_between_same_pair_is_auto_connected(db_session):
    shipper = User(telegram_id="tp_shipper_2", role=UserRole.SHIPPER, name="Shipper B", phone="0500000003")
    carrier = User(telegram_id="tp_carrier_2", role=UserRole.CARRIER, name="Carrier B", phone="0500000004")
    db_session.add_all([shipper, carrier])
    await db_session.flush()

    # Admin previously connected this exact pair (simulates the
    # api/admin.py "connect" action having run once before).
    await mark_trusted(db_session, shipper.id, carrier.id)
    await db_session.commit()
    assert await is_trusted_pair(db_session, shipper.id, carrier.id) is True

    load = Load(user_id=shipper.id, origin_city="Каракол", destination_city="Ош", status=LoadStatus.NEW)
    truck = Truck(user_id=carrier.id, current_city="Каракол", desired_destination="Ош")
    db_session.add_all([load, truck])
    await db_session.flush()

    match, created = await create_match_safely(db_session, load, truck, MatchResult(score=90, reasons=["test"]))
    assert created

    with patch("app.services.telegram_client.send_message", new=AsyncMock(return_value=True)) as mock_send:
        await process_new_match(db_session, load, truck, match, admin_id=None)
    await db_session.commit()

    assert match.status == MatchStatus.CONNECTED  # auto-connected, no admin wait
    assert load.status == LoadStatus.CONNECTED
    assert mock_send.call_count >= 2  # shipper + carrier both notified directly


@pytest.mark.asyncio
async def test_trust_is_scoped_to_the_specific_pair_not_global(db_session):
    """A carrier being trusted with one shipper must NOT auto-connect
    with a different shipper -- trust never generalizes."""
    shipper_a = User(telegram_id="tp_shipper_3", role=UserRole.SHIPPER, name="A")
    shipper_b = User(telegram_id="tp_shipper_4", role=UserRole.SHIPPER, name="B")
    carrier = User(telegram_id="tp_carrier_3", role=UserRole.CARRIER, name="C")
    db_session.add_all([shipper_a, shipper_b, carrier])
    await db_session.flush()

    await mark_trusted(db_session, shipper_a.id, carrier.id)
    await db_session.commit()

    assert await is_trusted_pair(db_session, shipper_a.id, carrier.id) is True
    assert await is_trusted_pair(db_session, shipper_b.id, carrier.id) is False


@pytest.mark.asyncio
async def test_mark_trusted_is_idempotent(db_session):
    shipper = User(telegram_id="tp_shipper_5", role=UserRole.SHIPPER)
    carrier = User(telegram_id="tp_carrier_5", role=UserRole.CARRIER)
    db_session.add_all([shipper, carrier])
    await db_session.flush()

    await mark_trusted(db_session, shipper.id, carrier.id)
    await db_session.commit()
    await mark_trusted(db_session, shipper.id, carrier.id)  # connect action run again later
    await db_session.commit()

    from sqlalchemy import func, select
    from app.models.models import TrustedPair
    count = (await db_session.execute(
        select(func.count()).select_from(TrustedPair).where(
            TrustedPair.shipper_user_id == shipper.id, TrustedPair.carrier_user_id == carrier.id
        )
    )).scalar_one()
    assert count == 1
