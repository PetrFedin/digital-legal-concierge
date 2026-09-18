from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings


# Test code intentionally enters many independent asyncio event loops (both
# pytest-asyncio tests and synchronous tests that call asyncio.run(...)).
# Async DB connections are loop-owned resources: retaining an asyncpg or
# aiosqlite connection in a pool after the loop that created it has closed can
# surface later as "Future attached to a different loop", "Event loop is
# closed", or an aiosqlite worker-thread warning in an unrelated test.
#
# Keep production/staging pooling unchanged. In APP_ENV=test use NullPool for
# every async backend so returning a connection from a session closes the
# physical connection instead of retaining it for a future test/event loop.
_engine_kwargs: dict[str, object] = {"echo": False}
if settings.app_env.strip().lower() == "test":
    _engine_kwargs["poolclass"] = NullPool

engine = create_async_engine(settings.database_url, **_engine_kwargs)

# ``expire_on_commit=False`` prevents SQLAlchemy from implicitly expiring every
# already-loaded scalar immediately after a successful commit. It is useful for
# short presentation snapshots, but it is deliberately NOT a licence to carry
# ORM entities across transaction boundaries. Product handlers must still copy
# every value required after commit()/rollback() into scalars/dataclasses before
# the boundary. Rollback can expire state independently, and a future session
# configuration change must not alter business correctness.
AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
