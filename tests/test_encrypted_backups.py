from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import settings
from app.security.backup_encryption import (
    BACKUP_SUFFIX,
    BackupSecurityError,
    cleanup_encrypted_backups,
    create_encrypted_backup,
    encrypt_backup_payload,
    extract_encrypted_backup,
    inspect_encrypted_backup,
    verify_encrypted_backup,
)

BACKUP_OLD = "backup-encryption-old-" + "a" * 40
BACKUP_NEW = "backup-encryption-new-" + "b" * 40


def configure_backup(monkeypatch, tmp_path, *, key_id="backups-old", key=BACKUP_OLD):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "allow_legacy_security_key_fallback", False)
    monkeypatch.setattr(settings, "backup_encryption_key_id", key_id)
    monkeypatch.setattr(settings, "backup_encryption_key", key)
    monkeypatch.setattr(settings, "backup_encryption_previous_keys", "")
    monkeypatch.setattr(settings, "max_backup_mb", 20)
    monkeypatch.setattr(settings, "backup_retention_days", 30)
    monkeypatch.setattr(settings, "backup_dir", str(tmp_path / "backups"))


def make_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute(
            "INSERT INTO evidence(value) VALUES (?)",
            ("highly confidential database value",),
        )
        connection.commit()


def test_create_verify_and_extract_encrypted_backup(tmp_path, monkeypatch):
    configure_backup(monkeypatch, tmp_path)
    database = tmp_path / "legal_bot.db"
    make_database(database)
    storage = tmp_path / "storage"
    document = storage / "cases" / "1" / "document.dlcenc"
    document.parent.mkdir(parents=True)
    document.write_bytes(b"encrypted document container bytes")
    quarantine = storage / "quarantine" / "rejected.pdf"
    quarantine.parent.mkdir(parents=True)
    quarantine.write_bytes(b"rejected upload must not be backed up")
    env_file = tmp_path / ".env"
    env_file.write_text("BACKUP_ENCRYPTION_KEY=must-not-enter-archive", encoding="utf-8")

    result = create_encrypted_backup(
        database_url=f"sqlite+aiosqlite:///{database}",
        storage_dir=storage,
        backup_dir=tmp_path / "backups",
    )
    archive = Path(result.path)

    assert result.verified is True
    assert archive.suffix == BACKUP_SUFFIX
    assert archive.stat().st_mode & 0o077 == 0
    raw = archive.read_bytes()
    assert b"highly confidential database value" not in raw
    assert b"encrypted document container bytes" not in raw
    assert BACKUP_OLD.encode() not in raw
    assert b"must-not-enter-archive" not in raw
    assert not list((tmp_path / "backups").glob("*.tar.gz"))

    inspected = inspect_encrypted_backup(archive)
    assert inspected.key_id == "backups-old"
    assert inspected.verified is False
    verified = verify_encrypted_backup(archive)
    assert verified.verified is True
    assert verified.plaintext_sha256 == result.plaintext_sha256

    restore = tmp_path / "restore-staging"
    restored_metadata = extract_encrypted_backup(archive, restore)
    assert restored_metadata.verified is True
    restored_db = restore / "database" / "legal_bot.db"
    with sqlite3.connect(restored_db) as connection:
        value = connection.execute("SELECT value FROM evidence WHERE id=1").fetchone()[0]
    assert value == "highly confidential database value"
    assert (restore / "storage" / "cases" / "1" / "document.dlcenc").read_bytes() == (
        b"encrypted document container bytes"
    )
    assert not (restore / "storage" / "quarantine").exists()
    assert not (restore / ".env").exists()
    manifest = json.loads((restore / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["secrets_included"] is False
    assert "quarantine" in manifest["storage"]["excluded_directories"]


def test_tampering_and_key_retirement_are_detected(tmp_path, monkeypatch):
    configure_backup(monkeypatch, tmp_path)
    database = tmp_path / "legal_bot.db"
    make_database(database)
    result = create_encrypted_backup(
        database_url=f"sqlite+aiosqlite:///{database}",
        storage_dir=tmp_path / "storage",
        backup_dir=tmp_path / "backups",
    )
    archive = Path(result.path)

    tampered = tmp_path / "backups" / "tampered.dlcbak"
    payload = bytearray(archive.read_bytes())
    payload[-1] ^= 1
    tampered.write_bytes(payload)
    with pytest.raises(BackupSecurityError, match="Целостность"):
        verify_encrypted_backup(tampered)

    monkeypatch.setattr(settings, "backup_encryption_key_id", "backups-new")
    monkeypatch.setattr(settings, "backup_encryption_key", BACKUP_NEW)
    monkeypatch.setattr(
        settings,
        "backup_encryption_previous_keys",
        f"backups-old:{BACKUP_OLD}",
    )
    assert verify_encrypted_backup(archive).verified is True

    monkeypatch.setattr(settings, "backup_encryption_previous_keys", "")
    with pytest.raises(BackupSecurityError, match="отсутствует"):
        verify_encrypted_backup(archive)


def test_restore_rejects_traversal_and_nonempty_destination(tmp_path, monkeypatch):
    configure_backup(monkeypatch, tmp_path)
    payload = tmp_path / "unsafe.tar.gz"
    content = b"escape"
    with tarfile.open(payload, "w:gz") as archive:
        member = tarfile.TarInfo("../escape.txt")
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    encrypted = tmp_path / "unsafe.dlcbak"
    encrypt_backup_payload(payload, encrypted)

    with pytest.raises(BackupSecurityError, match="небезопасный путь"):
        verify_encrypted_backup(encrypted)
    assert not (tmp_path / "escape.txt").exists()

    destination = tmp_path / "not-empty"
    destination.mkdir()
    (destination / "keep.txt").write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(BackupSecurityError, match="должен быть пустым"):
        extract_encrypted_backup(encrypted, destination)
    assert (destination / "keep.txt").read_text(encoding="utf-8") == "do not overwrite"


def test_cleanup_removes_only_old_encrypted_backups(tmp_path, monkeypatch):
    configure_backup(monkeypatch, tmp_path)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old = backup_dir / "old.dlcbak"
    recent = backup_dir / "recent.dlcbak"
    legacy = backup_dir / "legacy.tar.gz"
    for path in (old, recent, legacy):
        path.write_bytes(b"placeholder")
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=40)).timestamp()
    recent_timestamp = datetime.now(timezone.utc).timestamp()
    old.touch()
    recent.touch()
    legacy.touch()
    import os

    os.utime(old, (old_timestamp, old_timestamp))
    os.utime(recent, (recent_timestamp, recent_timestamp))
    os.utime(legacy, (old_timestamp, old_timestamp))

    assert cleanup_encrypted_backups(backup_dir, retention_days=30) == 1
    assert not old.exists()
    assert recent.exists()
    assert legacy.exists()
