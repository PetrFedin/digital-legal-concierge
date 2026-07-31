from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest

import app.security.backup_service as backup_service
from app.config import settings
from app.security.backup_encryption import (
    BackupSecurityError,
    extract_encrypted_backup,
)
from app.security.backup_service import (
    POSTGRES_DUMP_FORMAT,
    POSTGRES_DUMP_NAME,
    _snapshot_postgresql,
    create_provider_encrypted_backup,
)

BACKUP_KEY = "postgresql-encrypted-backup-" + "e" * 40
DATABASE_URL = (
    "postgresql+asyncpg://backup:p%3Aa%5Css@db.example:5433/concierge"
    "?sslmode=require&connect_timeout=7"
)


def configure(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "legal.txt").write_text("document", encoding="utf-8")
    backups = tmp_path / "backups"
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "allow_legacy_security_key_fallback", False)
    monkeypatch.setattr(settings, "backup_encryption_key_id", "postgres-backup-key")
    monkeypatch.setattr(settings, "backup_encryption_key", BACKUP_KEY)
    monkeypatch.setattr(settings, "backup_encryption_previous_keys", "")
    monkeypatch.setattr(settings, "max_backup_mb", 20)
    monkeypatch.setattr(settings, "storage_dir", str(storage))
    monkeypatch.setattr(settings, "backup_dir", str(backups))
    return storage, backups


def install_fake_postgres_tools(monkeypatch, calls: list[dict]) -> None:
    monkeypatch.setattr(
        backup_service.shutil,
        "which",
        lambda name: f"/usr/bin/{name}",
    )

    def fake_run(command, **kwargs):
        environment = dict(kwargs["env"])
        pgpass_path = environment.get("PGPASSFILE")
        pgpass_content = None
        pgpass_mode = None
        if pgpass_path:
            path = Path(pgpass_path)
            pgpass_content = path.read_text(encoding="utf-8")
            pgpass_mode = stat.S_IMODE(path.stat().st_mode)
        calls.append(
            {
                "command": list(command),
                "environment": environment,
                "pgpass_content": pgpass_content,
                "pgpass_mode": pgpass_mode,
            }
        )
        if Path(command[0]).name == "pg_dump":
            file_argument = next(
                item for item in command if item.startswith("--file=")
            )
            Path(file_argument.split("=", 1)[1]).write_bytes(b"PGDMPfake-custom-dump")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(backup_service.subprocess, "run", fake_run)


def test_postgresql_snapshot_keeps_password_out_of_process_arguments_and_env(
    tmp_path,
    monkeypatch,
):
    calls: list[dict] = []
    install_fake_postgres_tools(monkeypatch, calls)
    target = tmp_path / "database.dump"

    _snapshot_postgresql(DATABASE_URL, target, tmp_path)

    assert target.read_bytes().startswith(b"PGDMP")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [Path(call["command"][0]).name for call in calls] == [
        "pg_dump",
        "pg_restore",
    ]
    for call in calls:
        rendered = " ".join(call["command"])
        assert "p:a\\ss" not in rendered
        assert "p:a\\ss" not in json.dumps(call["environment"])
        assert "PGPASSWORD" not in call["environment"]
        assert call["environment"]["PGHOST"] == "db.example"
        assert call["environment"]["PGPORT"] == "5433"
        assert call["environment"]["PGDATABASE"] == "concierge"
        assert call["environment"]["PGUSER"] == "backup"
        assert call["environment"]["PGSSLMODE"] == "require"
        assert call["environment"]["PGCONNECT_TIMEOUT"] == "7"
        assert call["pgpass_mode"] == 0o600
        assert call["pgpass_content"] == (
            "db.example:5433:concierge:backup:p\\:a\\\\ss\n"
        )


def test_provider_builds_verified_postgresql_archive_without_pgpass(
    tmp_path,
    monkeypatch,
):
    storage, backups = configure(monkeypatch, tmp_path)
    calls: list[dict] = []
    install_fake_postgres_tools(monkeypatch, calls)

    result = create_provider_encrypted_backup(
        database_url=DATABASE_URL,
        storage_dir=storage,
        backup_dir=backups,
    )

    archive = Path(result.path)
    assert archive.exists()
    assert result.verified is True
    staging = tmp_path / "staging"
    metadata = extract_encrypted_backup(archive, staging)
    assert metadata.verified is True

    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["database"] == {
        "engine": "postgresql",
        "file": f"database/{POSTGRES_DUMP_NAME}",
        "format": POSTGRES_DUMP_FORMAT,
    }
    assert (staging / "database" / POSTGRES_DUMP_NAME).read_bytes().startswith(
        b"PGDMP"
    )
    assert (staging / "storage" / "legal.txt").read_text(encoding="utf-8") == (
        "document"
    )
    assert not list(staging.rglob(".pgpass"))
    restore_text = (staging / "restore" / "README.txt").read_text(
        encoding="utf-8"
    )
    assert "pg_restore --no-owner --no-privileges" in restore_text


def test_postgresql_tool_error_redacts_password(tmp_path, monkeypatch):
    monkeypatch.setattr(
        backup_service.shutil,
        "which",
        lambda name: f"/usr/bin/{name}",
    )

    def failed_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            2,
            stdout=b"",
            stderr=b"authentication failed for p:a\\ss",
        )

    monkeypatch.setattr(backup_service.subprocess, "run", failed_run)

    with pytest.raises(BackupSecurityError) as captured:
        _snapshot_postgresql(DATABASE_URL, tmp_path / "failed.dump", tmp_path)

    message = str(captured.value)
    assert "p:a\\ss" not in message
    assert "[REDACTED]" in message


def test_provider_rejects_unsupported_database_backend(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path)

    with pytest.raises(BackupSecurityError, match="не поддерживает СУБД mysql"):
        create_provider_encrypted_backup(
            database_url="mysql+pymysql://user:password@localhost/concierge",
            backup_dir=tmp_path / "backups",
            storage_dir=tmp_path / "storage",
        )
