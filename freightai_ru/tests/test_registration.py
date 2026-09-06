"""
Registration tests, per project rule 39: duplicate telegram_id must be
prevented (users.telegram_id is UNIQUE), and carrier/shipper registration
should each produce a correctly-roled user.
"""
import os
import sys

import pytest
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.models.models import User, UserRole
from app.services import db_utils
from sqlalchemy import select


@pytest.mark.asyncio
async def test_register_carrier(db_session):
    user = User(telegram_id="carrier_reg_1", role=UserRole.CARRIER, name="Ahmed")
    db_session.add(user)
    await db_session.commit()
    assert user.role == UserRole.CARRIER


@pytest.mark.asyncio
async def test_register_shipper(db_session):
    user = User(telegram_id="shipper_reg_1", role=UserRole.SHIPPER, name="Sara")
    db_session.add(user)
    await db_session.commit()
    assert user.role == UserRole.SHIPPER


@pytest.mark.asyncio
async def test_duplicate_telegram_id_prevented_via_get_or_create(db_session):
    stmt = select(User).where(User.telegram_id == "dup_1")

    def make_row():
        return User(telegram_id="dup_1", role=UserRole.UNSET)

    user1, created1 = await db_utils.get_or_create(db_session, stmt, make_row)
    await db_session.commit()
    user2, created2 = await db_utils.get_or_create(db_session, stmt, make_row)
    await db_session.commit()

    assert created1 is True
    assert created2 is False
    assert user1.id == user2.id


@pytest.mark.asyncio
async def test_raw_duplicate_insert_raises_integrity_error(db_session):
    """Confirms the DB-level constraint itself (not just app logic) is
    what prevents duplicates -- this is what create_match_safely /
    get_or_create rely on catching."""
    db_session.add(User(telegram_id="dup_2", role=UserRole.UNSET))
    await db_session.commit()

    db_session.add(User(telegram_id="dup_2", role=UserRole.UNSET))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
