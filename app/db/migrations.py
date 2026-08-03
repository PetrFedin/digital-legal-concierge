from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings


def _is_migration_root(path: Path) -> bool:
    return (path / "alembic.ini").is_file() and (path / "migrations").is_dir()


def resolve_project_root() -> Path:
    """Locate runtime Alembic files instead of assuming they live in the wheel."""

    configured = str(os.getenv("APP_PROJECT_ROOT", "")).strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())

    current = Path.cwd().resolve()
    candidates.extend((current, *current.parents))

    module_path = Path(__file__).resolve()
    candidates.extend(module_path.parents)
    candidates.append(Path("/app"))

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if _is_migration_root(resolved):
            return resolved

    checked = ", ".join(str(path) for path in seen)
    raise RuntimeError(
        "Не найдены alembic.ini и каталог migrations. "
        f"Проверенные каталоги: {checked}"
    )


def build_alembic_config(database_url: str | None = None) -> Config:
    project_root = resolve_project_root()
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    url = str(database_url or settings.database_url)
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["database_url_override"] = url
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
