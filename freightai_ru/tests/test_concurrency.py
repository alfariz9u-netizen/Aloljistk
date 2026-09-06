"""
Concurrency / duplicate-prevention tests, per project rule 39: creating
the same Match (or the same broadcast Notification) twice must result in
exactly ONE row, never several -- enforced by DB unique constraints, not
just application logic (see services/matching.py, services/notifications.py).
"""
import asyncio
import os
import sys

import pytest
from sqlalchemy import func, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.models.models import Load, LoadStatus, Match, Notification, NotificationType, Truck, User, UserRole
from app.services.matching import MatchResult, create_match_safely
from app.services.notifications import _create_notification_row


@pytest.mark.asyncio
async def test_concurrent_match_creation_yields_one_row(db_session):
    shipper = User(telegram_id="shipper1", role=UserRole.SHIPPER)
    carrier = User(telegram_id="carrier1", role=UserRole.CARRIER)
    db_session.add_all([shipper, carrier])
    await db_session.flush()

    load = Load(user_id=shipper.id, origin_city="Бишкек", destination_city="Ош", status=LoadStatus.WAITING_FOR_MATCH)
    truck = Truck(user_id=carrier.id, current_city="Бишкек", desired_destination="Ош")
    db_session.add_all([load, truck])
    await db_session.flush()

    result = MatchResult(score=90, reasons=["test"])

    # Simulate several concurrent attempts to create the SAME match
    # (e.g. a new-truck scan and a new-load scan racing each other).
    outcomes = await asyncio.gather(*[
        create_match_safely(db_session, load, truck, result) for _ in range(5)
    ])
    await db_session.commit()

    created_flags = [created for _, created in outcomes]
    assert created_flags.count(True) == 1  # exactly one request actually inserted

    count = (await db_session.execute(
        select(func.count()).select_from(Match).where(Match.load_id == load.id, Match.truck_id == truck.id)
    )).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_duplicate_broadcast_notification_not_created(db_session):
    shipper = User(telegram_id="shipper2", role=UserRole.SHIPPER)
    carrier = User(telegram_id="carrier2", role=UserRole.CARRIER)
    db_session.add_all([shipper, carrier])
    await db_session.flush()

    load = Load(user_id=shipper.id, origin_city="Каракол", destination_city="Ош", status=LoadStatus.WAITING_FOR_MATCH)
    db_session.add(load)
    await db_session.flush()

    first, created_first = await _create_notification_row(db_session, carrier.id, load.id, NotificationType.BROADCAST)
    second, created_second = await _create_notification_row(db_session, carrier.id, load.id, NotificationType.BROADCAST)
    await db_session.commit()

    assert created_first is True
    assert created_second is False
    assert first.id == second.id

    count = (await db_session.execute(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == carrier.id, Notification.load_id == load.id,
            Notification.notification_type == NotificationType.BROADCAST,
        )
    )).scalar_one()
    assert count == 1
