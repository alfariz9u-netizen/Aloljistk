from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import settings


class Base(DeclarativeBase):
    pass


# Supabase's "Transaction pooler" (PgBouncer in transaction mode, used by
# free-hosting deployments -- see README.md's "Free hosting" section) is,
# by Supabase's own description, meant for "stateless applications ...
# where each interaction with Postgres is brief and isolated." SQLAlchemy
# normally keeps its OWN pool of long-lived connections on top of that --
# double pooling -- which occasionally races: a connection held open by
# SQLAlchemy's pool can get silently reassigned to a different physical
# backend by PgBouncer between queries, so a previously-prepared
# statement name collides with one already prepared by a DIFFERENT
# client on that backend (DuplicatePreparedStatementError).
#
# Two changes together close this, matching Supabase's own guidance:
#   1. statement_cache_size=0 -- disables asyncpg's client-side prepared-
#      statement cache/naming (asyncpg's own documented fix for exactly
#      this PgBouncer scenario).
#   2. NullPool -- SQLAlchemy opens a fresh physical connection per
#      checkout and closes it on return, instead of holding a pool of
#      long-lived ones. This removes the double-pooling entirely and
#      matches "brief and isolated" usage. The cost is one extra
#      connection handshake per request, which is negligible against a
#      pooler (that's precisely what it's optimized for) and irrelevant
#      at MVP traffic levels.
#
# Both are unconditional (not just when a pooler is detected in the URL)
# because they're harmless against a normal direct Postgres connection
# too, and this keeps the code identical across the docker-compose/VPS
# path and the free-hosting path.
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    poolclass=NullPool,
    connect_args={"statement_cache_size": 0} if "asyncpg" in settings.database_url else {},
)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db():
    async with async_session() as session:
        yield session
