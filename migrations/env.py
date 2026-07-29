from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

runtime_url = (
    config.attributes.get("database_url_override")
    or os.getenv("DATABASE_URL")
    or config.get_main_option("sqlalchemy.url")
)
config.set_main_option("sqlalchemy.url", str(runtime_url).replace("%", "%%"))

target_metadata = Base.metadata


def compare_column_type(
    migration_context,
    inspected_column,
    metadata_column,
    inspected_type,
    metadata_type,
):
    """Keep PostgreSQL type checks strict, ignore SQLite affinity noise."""
    if migration_context.dialect.name == "sqlite":
        return False
    return None


def run_migrations_offline() -> None:
    is_sqlite = str(runtime_url).startswith("sqlite")
    context.configure(
        url=runtime_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=False if is_sqlite else True,
        compare_server_default=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=compare_column_type,
        compare_server_default=False,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
