"""
Notification tests, per project rule 39:
- no duplicate broadcast notification to the same user for the same load
- a user who already responded (INTERESTED) is not reminded again
- reminders are skipped once the load is no longer WAITING_FOR_MATCH
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.models.models import (
    Load, LoadStatus, Notification, NotificationStatus, NotificationType, User, UserRole,
)
from app.services import notifications


@pytest.mark.asyncio
async def test_reminder_skipped_if_already_interested(db_session):
    shipper = User(telegram_id="s_notif_1", role=UserRole.SHIPPER)
    carrier = User(telegram_id="c_notif_1", role=UserRole.CARRIER)
    db_session.add_all([shipper, carrier])
    await db_session.flush()

    load = Load(user_id=shipper.id, origin_city="Бишкек", destination_city="Ош", status=LoadStatus.WAITING_FOR_MATCH)
    db_session.add(load)
    await db_session.flush()

    notif = Notification(
        user_id=carrier.id, load_id=load.id, notification_type=NotificationType.BROADCAST,
        status=NotificationStatus.INTERESTED,  # carrier already responded
    )
    db_session.add(notif)
    await db_session.flush()

    with patch("app.services.telegram_client.send_message", new=AsyncMock(return_value=True)) as mock_send:
        await notifications.send_reminder(db_session, load, carrier, notif)
        mock_send.assert_not_called()  # status != SENT -> no reminder
    assert notif.reminder_sent is False


@pytest.mark.asyncio
async def test_reminder_sent_once_and_flag_set(db_session):
    shipper = User(telegram_id="s_notif_2", role=UserRole.SHIPPER)
    carrier = User(telegram_id="c_notif_2", role=UserRole.CARRIER)
    db_session.add_all([shipper, carrier])
    await db_session.flush()

    load = Load(user_id=shipper.id, origin_city="Каракол", destination_city="Ош", status=LoadStatus.WAITING_FOR_MATCH)
    db_session.add(load)
    await db_session.flush()

    notif = Notification(
        user_id=carrier.id, load_id=load.id, notification_type=NotificationType.BROADCAST,
        status=NotificationStatus.SENT,
        sent_at=datetime.now(timezone.utc) - timedelta(minutes=15),
    )
    db_session.add(notif)
    await db_session.flush()

    with patch("app.services.telegram_client.send_message", new=AsyncMock(return_value=True)) as mock_send:
        await notifications.send_reminder(db_session, load, carrier, notif)
        mock_send.assert_called_once()
    assert notif.reminder_sent is True

    # Calling again must NOT send a second reminder.
    with patch("app.services.telegram_client.send_message", new=AsyncMock(return_value=True)) as mock_send2:
        await notifications.send_reminder(db_session, load, carrier, notif)
        mock_send2.assert_not_called()


@pytest.mark.asyncio
async def test_no_duplicate_broadcast_across_two_calls(db_session):
    shipper = User(telegram_id="s_notif_3", role=UserRole.SHIPPER)
    carrier = User(telegram_id="c_notif_3", role=UserRole.CARRIER)
    db_session.add_all([shipper, carrier])
    await db_session.flush()

    load = Load(user_id=shipper.id, origin_city="Токмок", destination_city="Ош", status=LoadStatus.WAITING_FOR_MATCH)
    db_session.add(load)
    await db_session.flush()

    with patch("app.services.telegram_client.send_message", new=AsyncMock(return_value=True)):
        first = await notifications.broadcast_load_to_carrier(db_session, load, carrier)
        await db_session.commit()
        second = await notifications.broadcast_load_to_carrier(db_session, load, carrier)
        await db_session.commit()

    assert first is not None
    assert second is None  # already notified -- no duplicate row/send
