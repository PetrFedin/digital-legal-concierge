from __future__ import annotations

import asyncio
import gc
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


ROOT = Path(__file__).resolve().parents[1]


def test_pytest_asyncio_uses_one_process_lifetime_owner_loop() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'asyncio_default_test_loop_scope = "session"' in pyproject
    assert 'asyncio_default_fixture_loop_scope = "session"' in pyproject
    assert 'filterwarnings = [\n  "error",' in pyproject


@pytest.mark.asyncio
async def test_repeated_aiosqlite_engine_close_finishes_before_owner_loop_exit(
    tmp_path: Path,
) -> None:
    """Focused PM-043 reproduction: no worker may need a destroyed test loop."""

    owner_loop = asyncio.get_running_loop()
    for index in range(25):
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / f'pm043-{index}.db'}",
            poolclass=NullPool,
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE probe (id INTEGER PRIMARY KEY)"))
        async with factory() as session:
            await session.execute(text("INSERT INTO probe DEFAULT VALUES"))
            await session.commit()
            value = await session.scalar(text("SELECT COUNT(*) FROM probe"))
            assert value == 1
        await engine.dispose()

    # Force Python/SQLAlchemy/aiosqlite finalizers while the pytest-owned loop
    # is still alive. Warnings are errors, so any escaped worker exception fails.
    gc.collect()
    await asyncio.sleep(0)
    assert asyncio.get_running_loop() is owner_loop
