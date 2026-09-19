from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db.session import AsyncSessionLocal, engine


def test_application_test_engine_never_reuses_async_connections_between_loops() -> None:
    assert isinstance(engine.pool, NullPool)


def test_test_owned_aiosqlite_engine_defaults_to_null_pool() -> None:
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        assert isinstance(test_engine.pool, NullPool)
    finally:
        asyncio.run(test_engine.dispose())


def test_application_test_session_executes_in_sequential_fresh_event_loops() -> None:
    async def probe() -> int:
        async with AsyncSessionLocal() as db:
            value = (await db.execute(text("SELECT 1"))).scalar_one()
            return int(value)

    assert asyncio.run(probe()) == 1
    assert asyncio.run(probe()) == 1
