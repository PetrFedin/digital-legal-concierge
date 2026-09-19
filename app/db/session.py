from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings


# Pytest intentionally mixes async tests with synchronous tests that own fresh
# ``asyncio.run(...)`` loops. Reusing any async DBAPI connection after the
# event loop that created it has closed can fail outside the business assertion:
# asyncpg reports cross-loop futures, while aiosqlite worker threads can surface
# "Event loop is closed" / unclosed-connection warnings during later test
# setup/teardown. In APP_ENV=test every AsyncSession therefore receives a fresh
# physical connection. Production/staging pooling is unchanged.
if settings.app_env.strip().lower() == "test":
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        poolclass=NullPool,
    )
else:
    engine = create_async_engine(settings.database_url, echo=False)

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
