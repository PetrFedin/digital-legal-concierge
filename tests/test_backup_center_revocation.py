from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.api.backup_center import (
    BACKUP_CENTER_HTML,
    _verify_restorable_archive,
    backup_inventory,
)
from app.config import settings
from app.security.backup_encryption import create_encrypted_backup
from app.security.backup_restore_assessment import BackupRevokedError
from app.security.backup_restore_fence import (
    FENCE_FILE_NAME,
    BackupRestoreFenceError,
    advance_backup_restore_fence,
    backup_maintenance_lock,
)

BACKUP_KEY = "backup-center-revocation-" + "c" * 40


def configure(monkeypatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    database = tmp_path / "concierge.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE marker (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO marker(value) VALUES ('safe')")
        connection.commit()
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "document.txt").write_text("legal document", encoding="utf-8")
    backup_dir = tmp_path / "backups"

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "allow_legacy_security_key_fallback", False)
    monkeypatch.setattr(settings, "backup_encryption_key_id", "backup-center-key")
    monkeypatch.setattr(settings, "backup_encryption_key", BACKUP_KEY)
    monkeypatch.setattr(settings, "backup_encryption_previous_keys", "")
    monkeypatch.setattr(
        settings,
        "database_url",
        f"sqlite+aiosqlite:///{database}",
    )
    monkeypatch.setattr(settings, "storage_dir", str(storage))
    monkeypatch.setattr(settings, "backup_dir", str(backup_dir))
    monkeypatch.setattr(settings, "max_backup_mb", 20)
    monkeypatch.setattr(settings, "backup_retention_days", 30)
    return database, storage, backup_dir


def create_valid_backup(monkeypatch, tmp_path: Path):
    _, storage, backup_dir = configure(monkeypatch, tmp_path)
    result = create_encrypted_backup(
        database_url=settings.database_url,
        storage_dir=storage,
        backup_dir=backup_dir,
    )
    return backup_dir, Path(result.path), result


def revoke_archive(backup_dir: Path, result) -> None:
    cutoff = datetime.fromisoformat(result.created_at) + timedelta(microseconds=1)
    with backup_maintenance_lock(backup_dir):
        advance_backup_restore_fence(
            cutoff,
            reason="Case content cryptographic erasure",
            event_id="case:7:retention:41",
            backup_dir=backup_dir,
        )


def test_inventory_distinguishes_restorable_and_revoked_archives(tmp_path, monkeypatch):
    backup_dir, archive, result = create_valid_backup(monkeypatch, tmp_path)

    before = backup_inventory()
    assert before["ok"] is True
    assert before["restorable_encrypted_backups_count"] == 1
    assert before["revoked_encrypted_backups_count"] == 0
    assert before["latest"][0]["restorable"] is True
    assert before["latest"][0]["revoked"] is False

    verified = _verify_restorable_archive(archive, backup_dir)
    assert verified.verified is True

    revoke_archive(backup_dir, result)
    after = backup_inventory()
    assert after["ok"] is False
    assert after["restore_fence_valid"] is True
    assert after["restorable_encrypted_backups_count"] == 0
    assert after["revoked_encrypted_backups_count"] == 1
    assert after["latest"][0]["restorable"] is False
    assert after["latest"][0]["revoked"] is True
    assert (
        after["latest"][0]["restore_block_reason"]
        == "revoked_by_cryptographic_erasure"
    )

    with pytest.raises(BackupRevokedError, match="отозвана"):
        _verify_restorable_archive(archive, backup_dir)


def test_invalid_restore_fence_blocks_all_backup_operations(tmp_path, monkeypatch):
    backup_dir, archive, result = create_valid_backup(monkeypatch, tmp_path)
    revoke_archive(backup_dir, result)

    fence_path = backup_dir / FENCE_FILE_NAME
    payload = json.loads(fence_path.read_text(encoding="utf-8"))
    payload["signature"] = "0" * 64
    fence_path.write_text(json.dumps(payload), encoding="utf-8")

    inventory = backup_inventory()
    assert inventory["ok"] is False
    assert inventory["backup_operations_available"] is False
    assert inventory["restore_fence_present"] is True
    assert inventory["restore_fence_valid"] is False
    assert inventory["restorable_encrypted_backups_count"] == 0
    assert inventory["latest"][0]["restorable"] is False
    assert inventory["latest"][0]["restore_block_reason"] == "restore_fence_invalid"

    with pytest.raises(BackupRestoreFenceError, match="Подпись"):
        _verify_restorable_archive(archive, backup_dir)


def test_backup_center_ui_disables_non_restorable_archive_actions():
    assert "x.revoked" in BACKUP_CENTER_HTML
    assert "x.restorable?'':'disabled'" in BACKUP_CENTER_HTML
    assert "button[data-archive]:not([disabled])" in BACKUP_CENTER_HTML
    assert "Restore-fence" in BACKUP_CENTER_HTML
