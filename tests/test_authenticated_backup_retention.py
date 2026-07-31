from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import app.security.backup_encryption as backup_encryption
from app.config import settings
from app.security.backup_encryption import encrypt_backup_payload
from app.security.backup_retention import cleanup_authenticated_backups

BACKUP_KEY = "authenticated-backup-retention-" + "d" * 40


class FrozenDateTime(datetime):
    current = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        value = cls.current
        if tz is None:
            return value.replace(tzinfo=None)
        return value.astimezone(tz)


def configure(monkeypatch, tmp_path: Path) -> Path:
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "allow_legacy_security_key_fallback", False)
    monkeypatch.setattr(settings, "backup_encryption_key_id", "retention-key")
    monkeypatch.setattr(settings, "backup_encryption_key", BACKUP_KEY)
    monkeypatch.setattr(settings, "backup_encryption_previous_keys", "")
    monkeypatch.setattr(settings, "backup_dir", str(backup_dir))
    monkeypatch.setattr(settings, "backup_retention_days", 30)
    monkeypatch.setattr(settings, "max_backup_mb", 20)
    monkeypatch.setattr(backup_encryption, "datetime", FrozenDateTime)
    return backup_dir


def encrypted_file(
    tmp_path: Path,
    backup_dir: Path,
    name: str,
    created_at: datetime,
):
    payload = tmp_path / f"{name}.payload"
    payload.write_bytes(f"payload:{name}".encode("utf-8"))
    FrozenDateTime.current = created_at
    archive = backup_dir / f"{name}.dlcbak"
    metadata = encrypt_backup_payload(payload, archive)
    return archive, metadata


def test_cleanup_uses_authenticated_created_at_not_filesystem_mtime(
    tmp_path,
    monkeypatch,
):
    backup_dir = configure(monkeypatch, tmp_path)
    now = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)
    old_archive, old_metadata = encrypted_file(
        tmp_path,
        backup_dir,
        "old",
        now - timedelta(days=40),
    )
    recent_archive, recent_metadata = encrypted_file(
        tmp_path,
        backup_dir,
        "recent",
        now - timedelta(days=2),
    )

    # Deliberately reverse the filesystem timestamps. A mutable mtime must not
    # keep the old archive alive or remove the recent one.
    os.utime(old_archive, (now.timestamp(), now.timestamp()))
    stale_mtime = (now - timedelta(days=90)).timestamp()
    os.utime(recent_archive, (stale_mtime, stale_mtime))

    assert datetime.fromisoformat(old_metadata.created_at) < now - timedelta(days=30)
    assert datetime.fromisoformat(recent_metadata.created_at) > now - timedelta(days=30)
    assert cleanup_authenticated_backups(
        backup_dir,
        retention_days=30,
        now=now,
    ) == 1
    assert not old_archive.exists()
    assert recent_archive.exists()


def test_tampered_created_at_is_retained_for_investigation(tmp_path, monkeypatch):
    backup_dir = configure(monkeypatch, tmp_path)
    now = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)
    archive, metadata = encrypted_file(tmp_path, backup_dir, "tampered", now)

    original = metadata.created_at.encode("utf-8")
    forged = (now - timedelta(days=100)).isoformat().encode("utf-8")
    assert len(original) == len(forged)
    payload = archive.read_bytes()
    assert original in payload
    archive.write_bytes(payload.replace(original, forged, 1))
    stale_mtime = (now - timedelta(days=100)).timestamp()
    os.utime(archive, (stale_mtime, stale_mtime))

    # The header is AES-GCM AAD. Changing created_at invalidates the tag, so
    # cleanup must not trust the forged age and must leave the file visible to
    # Backup Center as unreadable/tampered.
    assert cleanup_authenticated_backups(
        backup_dir,
        retention_days=30,
        now=now,
    ) == 0
    assert archive.exists()


def test_scheduler_runs_authenticated_cleanup_outside_async_event_loop():
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "scheduler"
        / "jobs.py"
    ).read_text(encoding="utf-8")

    assert "from app.security.backup_retention import cleanup_authenticated_backups" in source
    assert "await asyncio.to_thread(\n                cleanup_authenticated_backups" in source
    assert "from app.security.backup_encryption import cleanup_encrypted_backups" not in source
