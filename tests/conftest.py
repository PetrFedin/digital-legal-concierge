from __future__ import annotations

from typing import Any

import sqlalchemy.ext.asyncio as sqlalchemy_asyncio
from sqlalchemy.pool import NullPool


_ORIGINAL_CREATE_ASYNC_ENGINE = sqlalchemy_asyncio.create_async_engine


def _isolated_test_create_async_engine(url: Any, *args: Any, **kwargs: Any):
    """Prevent test-owned aiosqlite connections from surviving their event loop.

    SQLAlchemy's current file-backed aiosqlite default is an async queue pool.
    A large part of this historical suite creates a local engine and disposes it
    only after all assertions. When an assertion exits early, the pool can keep
    a worker connection alive until pytest has already closed that test's event
    loop. The next test then receives an unrelated ResourceWarning/thread error.

    During pytest collection only, default SQLite/aiosqlite engines to NullPool.
    Tests that intentionally exercise a specific pool remain free to pass an
    explicit poolclass. Application production/staging construction is not
    modified by this test-only hook.
    """

    if str(url).startswith("sqlite+aiosqlite") and "poolclass" not in kwargs:
        kwargs["poolclass"] = NullPool
    return _ORIGINAL_CREATE_ASYNC_ENGINE(url, *args, **kwargs)


# conftest.py is imported before test modules, so historical
# from sqlalchemy.ext.asyncio import create_async_engine statements receive
# the isolated constructor without editing hundreds of unrelated test files.
sqlalchemy_asyncio.create_async_engine = _isolated_test_create_async_engine
