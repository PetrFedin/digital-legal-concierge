from __future__ import annotations

from typing import Any

import sqlalchemy.ext.asyncio as sqlalchemy_asyncio
from sqlalchemy.pool import NullPool


_ORIGINAL_CREATE_ASYNC_ENGINE = sqlalchemy_asyncio.create_async_engine


def _is_file_backed_aiosqlite(url: Any) -> bool:
    value = str(url)
    if not value.startswith("sqlite+aiosqlite"):
        return False
    # In-memory SQLite intentionally relies on SQLAlchemy's StaticPool so schema
    # and data survive across logical connections of the same engine. Forcing
    # NullPool here would create a new empty database for every checkout.
    return ":memory:" not in value


def _isolated_test_create_async_engine(url: Any, *args: Any, **kwargs: Any):
    """Prevent file-backed test aiosqlite connections surviving their event loop.

    Historical tests frequently create a file-backed async engine and dispose it
    only after all assertions. If an assertion exits early, an async queue pool
    can retain a worker connection until pytest closes that test's event loop.
    The next unrelated test then receives ResourceWarning/thread exceptions.

    During pytest collection only, file-backed SQLite/aiosqlite engines default
    to NullPool unless the test explicitly requests another pool. In-memory
    SQLite keeps SQLAlchemy's normal StaticPool semantics. Production/staging
    construction is not modified by this test-only hook.
    """

    if _is_file_backed_aiosqlite(url) and "poolclass" not in kwargs:
        kwargs["poolclass"] = NullPool
    return _ORIGINAL_CREATE_ASYNC_ENGINE(url, *args, **kwargs)


# conftest.py is imported before test modules, so historical
# from sqlalchemy.ext.asyncio import create_async_engine statements receive
# the isolated constructor without editing hundreds of unrelated test files.
sqlalchemy_asyncio.create_async_engine = _isolated_test_create_async_engine
