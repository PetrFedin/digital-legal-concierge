from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


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
