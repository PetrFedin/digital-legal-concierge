from __future__ import annotations

import os

import sqlalchemy.ext.asyncio as sqlalchemy_asyncio
from sqlalchemy.pool import NullPool


_ORIGINAL_CREATE_ASYNC_ENGINE = sqlalchemy_asyncio.create_async_engine


def _test_create_async_engine(url, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
    """Prevent test-created aiosqlite pools from outliving pytest event loops.

    Pytest loads this conftest before importing test modules. Legacy tests that
    import create_async_engine from sqlalchemy.ext.asyncio therefore receive
    this wrapper. Production application processes never import tests/conftest.py.
    """

    if (
        os.environ.get("APP_ENV", "").strip().lower() == "test"
        and str(url).startswith("sqlite+aiosqlite:")
    ):
        kwargs.setdefault("poolclass", NullPool)
    return _ORIGINAL_CREATE_ASYNC_ENGINE(url, *args, **kwargs)


if os.environ.get("APP_ENV", "").strip().lower() == "test":
    sqlalchemy_asyncio.create_async_engine = _test_create_async_engine
