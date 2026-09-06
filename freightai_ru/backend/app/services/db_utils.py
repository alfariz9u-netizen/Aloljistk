"""Race-safe get-or-create, used anywhere a unique row must not be
duplicated under concurrent requests (see models.py's UniqueConstraints).
Uses a SAVEPOINT so a conflict only rolls back this one insert attempt."""
from typing import Callable, TypeVar

from sqlalchemy import Select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


async def get_or_create(db: AsyncSession, select_stmt: Select, make_row: Callable[[], T]) -> tuple[T, bool]:
    result = await db.execute(select_stmt)
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing, False

    row = make_row()
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
        return row, True
    except IntegrityError:
        result = await db.execute(select_stmt)
        return result.scalar_one(), False
