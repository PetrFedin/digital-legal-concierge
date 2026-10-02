from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db.migrations import run_database_migrations


HEAD_REVISION = "20261001_0032"


def _url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def _columns(path: Path, table: str) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {
            row[1]
            for row in connection.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
        }


def _indexes(path: Path, table: str) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {
            row[1]
            for row in connection.execute(
                f"PRAGMA index_list({table})"
            ).fetchall()
        }


def test_dlc_int_01_migration_creates_derivative_authority_and_retention_counter(
    tmp_path: Path,
) -> None:
    database = tmp_path / "dlc-int-01-migration.db"
    run_database_migrations(database_url=_url(database))
    run_database_migrations(database_url=_url(database))

    with sqlite3.connect(database) as connection:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    assert revision == HEAD_REVISION
    assert "document_derivatives" in tables
    assert "derivatives_deleted" in _columns(
        database,
        "case_retention_records",
    )
    assert {
        "case_id",
        "source_document_id",
        "derivative_type",
        "status",
        "source_sha256",
        "file_path",
        "sha256",
        "tool_name",
        "tool_version",
        "recipe_id",
        "provenance",
        "page_count",
        "has_usable_text",
        "encryption_status",
        "encryption_key_id",
        "encryption_format_version",
        "encryption_envelope_id",
        "encrypted_data_key",
        "encrypted_data_key_nonce",
        "data_key_destroyed_at",
        "encrypted_at",
        "error_code",
        "error_detail",
    }.issubset(_columns(database, "document_derivatives"))
    assert {
        "ux_document_derivatives_encryption_envelope_id",
        "ix_document_derivatives_source_type",
        "ix_document_derivatives_case_status",
    }.issubset(_indexes(database, "document_derivatives"))
