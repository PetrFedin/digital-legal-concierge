from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.security.backup_cli import build_parser
from app.security.postgresql_restore import (
    PostgreSQLRestoreError,
    _manifest_database,
    validate_staging_target,
)

PRODUCTION = (
    "postgresql+asyncpg://app:secret@database.internal:5432/concierge"
)


def test_staging_target_requires_exact_database_confirmation():
    with pytest.raises(PostgreSQLRestoreError, match="Подтверждение"):
        validate_staging_target(
            "postgresql+asyncpg://app:secret@database.internal:5432/concierge_restore",
            confirmed_database="concierge_restore_typo",
            production_database_url=PRODUCTION,
        )


def test_staging_target_rejects_production_endpoint():
    with pytest.raises(PostgreSQLRestoreError, match="рабочую"):
        validate_staging_target(
            PRODUCTION,
            confirmed_database="concierge",
            production_database_url=PRODUCTION,
        )


def test_staging_target_requires_explicit_safe_suffix():
    with pytest.raises(PostgreSQLRestoreError, match="оканчиваться"):
        validate_staging_target(
            "postgresql+asyncpg://app:secret@database.internal:5432/concierge_copy",
            confirmed_database="concierge_copy",
            production_database_url=PRODUCTION,
        )


@pytest.mark.parametrize("database", ["postgres", "template0", "template1"])
def test_staging_target_rejects_system_databases(database):
    with pytest.raises(PostgreSQLRestoreError, match="Системная"):
        validate_staging_target(
            f"postgresql+asyncpg://app:secret@database.internal:5432/{database}",
            confirmed_database=database,
            production_database_url=PRODUCTION,
        )


def test_staging_target_accepts_separate_restore_database():
    target = validate_staging_target(
        "postgresql+asyncpg://restore:secret@restore.internal:5432/concierge_restore",
        confirmed_database="concierge_restore",
        production_database_url=PRODUCTION,
    )

    assert target.database == "concierge_restore"
    assert target.host == "restore.internal"


def test_manifest_requires_exact_postgresql_provider_contract(tmp_path: Path):
    destination = tmp_path / "staging"
    database_dir = destination / "database"
    database_dir.mkdir(parents=True)
    dump = database_dir / "database.dump"
    dump.write_bytes(b"PGDMPverified")
    (destination / "manifest.json").write_text(
        json.dumps(
            {
                "database": {
                    "engine": "postgresql",
                    "file": "database/database.dump",
                    "format": "pg_dump_custom",
                }
            }
        ),
        encoding="utf-8",
    )

    assert _manifest_database(destination) == dump

    payload = json.loads((destination / "manifest.json").read_text())
    payload["database"]["file"] = "../outside.dump"
    (destination / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PostgreSQLRestoreError, match="поддерживаемый"):
        _manifest_database(destination)


def test_staging_restore_cli_never_accepts_database_url_argument():
    parser = build_parser()
    args = parser.parse_args(
        [
            "restore-postgresql-staging",
            "backup.dlcbak",
            "/tmp/restore",
            "--confirm-database",
            "concierge_restore",
        ]
    )

    assert args.command == "restore-postgresql-staging"
    assert not hasattr(args, "database_url")

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "restore-postgresql-staging",
                "backup.dlcbak",
                "/tmp/restore",
                "--database-url",
                "postgresql://secret@host/concierge_restore",
                "--confirm-database",
                "concierge_restore",
            ]
        )


def test_restore_uses_single_transaction_and_no_password_process_argument():
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "security"
        / "postgresql_restore.py"
    ).read_text(encoding="utf-8")

    assert '"--single-transaction"' in source
    assert '"--exit-on-error"' in source
    assert '"--no-password"' in source
    assert "STAGING_DATABASE_URL" not in source
    assert "target.password" not in " ".join(
        line for line in source.splitlines() if "command =" in line
    )
