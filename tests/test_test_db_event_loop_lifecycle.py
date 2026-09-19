from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db.session import AsyncSessionLocal, engine


def test_application_test_engine_never_reuses_async_connections_between_loops() -> None:
    assert isinstance(engine.pool, NullPool)


def test_file_backed_test_aiosqlite_engine_defaults_to_null_pool(tmp_path) -> None:
    database_path = tmp_path / "direct-engine.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    try:
        assert isinstance(test_engine.pool, NullPool)
    finally:
        asyncio.run(test_engine.dispose())


def test_in_memory_aiosqlite_keeps_shared_engine_database_semantics() -> None:
    async def probe() -> None:
        test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with test_engine.begin() as connection:
                await connection.execute(text("CREATE TABLE lifecycle_probe (id INTEGER)"))
            async with test_engine.begin() as connection:
                await connection.execute(text("INSERT INTO lifecycle_probe (id) VALUES (1)"))
            async with test_engine.connect() as connection:
                value = (
                    await connection.execute(text("SELECT COUNT(*) FROM lifecycle_probe"))
                ).scalar_one()
                assert int(value) == 1
        finally:
            await test_engine.dispose()

    asyncio.run(probe())


def test_application_test_session_executes_in_sequential_fresh_event_loops() -> None:
    async def probe() -> int:
        async with AsyncSessionLocal() as db:
            value = (await db.execute(text("SELECT 1"))).scalar_one()
            return int(value)

    assert asyncio.run(probe()) == 1
    assert asyncio.run(probe()) == 1
