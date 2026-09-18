from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.session import AsyncSessionLocal, engine


pytestmark = pytest.mark.skipif(
    settings.app_env.strip().lower() != "test",
    reason="PM-021 lifecycle proof targets APP_ENV=test only",
)


def test_application_test_engine_never_reuses_async_connections_between_tests() -> None:
    assert isinstance(engine.sync_engine.pool, NullPool)


def test_legacy_test_engine_factory_is_forced_to_nullpool() -> None:
    local_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    assert isinstance(local_engine.sync_engine.pool, NullPool)

    async def ping_and_close() -> None:
        async with local_engine.connect() as connection:
            assert await connection.scalar(text("SELECT 1")) == 1
        await local_engine.dispose()

    asyncio.run(ping_and_close())


def test_application_test_engine_is_safe_across_sequential_fresh_event_loops() -> None:
    async def ping() -> None:
        async with AsyncSessionLocal() as session:
            assert await session.scalar(text("SELECT 1")) == 1

    # This is the exact lifecycle shape used by many legacy synchronous tests:
    # each call owns and closes a fresh loop. A pooled async DB connection must
    # not survive from one call into the next one.
    asyncio.run(ping())
    asyncio.run(ping())
    asyncio.run(engine.dispose())
