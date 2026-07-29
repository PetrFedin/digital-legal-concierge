from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG_PATH = PROJECT_ROOT / "alembic.ini"
MIGRATIONS_PATH = PROJECT_ROOT / "migrations"


def build_alembic_config(database_url: str | None = None) -> Config:
    config = Config(str(ALEMBIC_CONFIG_PATH))
    config.set_main_option("script_location", str(MIGRATIONS_PATH))
    url = database_url or settings.database_url
    config.set_main_option("sqlalchemy.url", str(url).replace("%", "%%"))
    return config


def run_database_migrations(
    revision: str = "head",
    *,
    database_url: str | None = None,
) -> None:
    command.upgrade(
        build_alembic_config(database_url),
        revision,
    )


def current_database_revision(
    *,
    database_url: str | None = None,
) -> None:
    command.current(
        build_alembic_config(database_url),
        verbose=True,
    )
