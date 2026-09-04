from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings


# PostgreSQL integration tests intentionally run multiple synchronous pytest
# functions that each own an ``asyncio.run(...)`` loop. Reusing an asyncpg
# pooled connection after its original loop has closed makes later race tests
# fail before exercising the domain contract ("Future attached to a different
# loop" / "another operation is in progress"). Keep production/staging pooling
# unchanged; in the test environment each PostgreSQL AsyncSession receives a
# fresh physical connection while the concurrency scenarios still use real,
# independent PostgreSQL transactions and row locks.
if (
    settings.app_env.strip().lower() == "test"
    and settings.database_url.startswith(("postgresql", "postgres"))
):
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
