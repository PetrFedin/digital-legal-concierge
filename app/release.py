from __future__ import annotations

import os
from functools import lru_cache

from alembic.script import ScriptDirectory

from app.db.migrations import build_alembic_config


APPLICATION_VERSION = "1.0.0-v45"
UNKNOWN_VALUE = "unknown"


def _clean_environment_value(name: str, default: str = UNKNOWN_VALUE) -> str:
    value = str(os.getenv(name, "")).strip()
    return value or default


@lru_cache(maxsize=1)
def expected_migration_heads() -> tuple[str, ...]:
    script = ScriptDirectory.from_config(build_alembic_config())
    return tuple(sorted(script.get_heads()))


def release_metadata() -> dict[str, object]:
    git_commit = _clean_environment_value("GIT_COMMIT_SHA")
    return {
        "application_version": APPLICATION_VERSION,
        "release": _clean_environment_value("APP_RELEASE", git_commit),
        "git_commit": git_commit,
        "build_timestamp": _clean_environment_value("BUILD_TIMESTAMP"),
        "image_repository": _clean_environment_value("APP_IMAGE_REPOSITORY"),
        "image_tag": _clean_environment_value("APP_IMAGE_TAG"),
        "migration_heads": list(expected_migration_heads()),
    }


__all__ = [
    "APPLICATION_VERSION",
    "expected_migration_heads",
    "release_metadata",
]
