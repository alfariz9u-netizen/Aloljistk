"""
Shared fixtures. DB-backed tests use an in-memory SQLite database via
aiosqlite -- fast, no external service needed for the test suite -- while
production always runs on PostgreSQL (see backend/app/core/config.py).
SQLite supports the UniqueConstraint + IntegrityError mechanics these
tests exercise, which is what actually matters for the concurrency
guarantees being tested here.
"""
import asyncio
import os
import sys

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("BOT_SERVICE_SECRET", "test-secret")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.database import Base  # noqa: E402
from app.models import models  # noqa: E402  -- register tables


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session
    await engine.dispose()
