from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
import app.security.backup_encryption as backup_encryption
import app.security.backup_freshness as freshness_module
from app.config import settings
from app.security.backup_encryption import create_encrypted_backup
from app.security.backup_freshness import (
    BackupFreshnessStatus,
    backup_freshness_status,
    clear_backup_freshness_cache,
)
from app.security.backup_restore_fence import (
    advance_backup_restore_fence,
    backup_maintenance_lock,
)

BACKUP_KEY = "backup-freshness-readiness-" + "f" * 40


class FrozenDateTime(datetime):
    current = datetime(2026, 7, 31, 8, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        value = cls.current
        if tz is None:
            return value.replace(tzinfo=None)
        return value.astimezone(tz)


def configure(monkeypatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    database = tmp_path / "concierge.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE marker (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO marker(value) VALUES ('fresh')")
        connection.commit()
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "document.txt").write_text("legal document", encoding="utf-8")
    backups = tmp_path / "backups"

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "allow_legacy_security_key_fallback", False)
    monkeypatch.setattr(settings, "backup_encryption_key_id", "freshness-key")
    monkeypatch.setattr(settings, "backup_encryption_key", BACKUP_KEY)
    monkeypatch.setattr(settings, "backup_encryption_previous_keys", "")
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{database}")
    monkeypatch.setattr(settings, "storage_dir", str(storage))
    monkeypatch.setattr(settings, "backup_dir", str(backups))
    monkeypatch.setattr(settings, "max_backup_mb", 20)
    monkeypatch.setattr(settings, "backup_max_age_hours", 26)
    monkeypatch.setattr(settings, "backup_freshness_cache_seconds", 300)
    monkeypatch.setattr(settings, "backup_future_clock_skew_seconds", 300)
    monkeypatch.setattr(settings, "backup_readiness_required_in_production", True)
    monkeypatch.setattr(backup_encryption, "datetime", FrozenDateTime)
    clear_backup_freshness_cache()
    return database, storage, backups


def create_backup(
    monkeypatch,
    tmp_path: Path,
    *,
    created_at: datetime,
):
    _, storage, backups = configure(monkeypatch, tmp_path)
    FrozenDateTime.current = created_at
    result = create_encrypted_backup(
        database_url=settings.database_url,
        storage_dir=storage,
        backup_dir=backups,
    )
    return backups, Path(result.path), result


def test_fresh_fully_verified_backup_satisfies_production_readiness(
    tmp_path,
    monkeypatch,
):
    created_at = datetime(2026, 7, 31, 8, 0, 0, tzinfo=timezone.utc)
    backups, archive, _ = create_backup(
        monkeypatch,
        tmp_path,
        created_at=created_at,
    )

    status = backup_freshness_status(
        backup_dir=backups,
        required=True,
        max_age_hours=26,
        now=created_at + timedelta(hours=2),
    )

    assert status.ok is True
    assert status.verified is True
    assert status.archive == archive.name
    assert status.age_seconds == 7200
    assert status.reason is None


def test_verified_backup_becomes_stale_without_redecrypting_unchanged_archive(
    tmp_path,
    monkeypatch,
):
    created_at = datetime(2026, 7, 30, 8, 0, 0, tzinfo=timezone.utc)
    backups, _, _ = create_backup(monkeypatch, tmp_path, created_at=created_at)
    calls = 0
    original = freshness_module.verify_encrypted_backup

    def counted_verify(path):
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(freshness_module, "verify_encrypted_backup", counted_verify)
    fresh = backup_freshness_status(
        backup_dir=backups,
        required=True,
        max_age_hours=26,
        cache_seconds=300,
        now=created_at + timedelta(hours=25),
    )
    stale = backup_freshness_status(
        backup_dir=backups,
        required=True,
        max_age_hours=26,
        cache_seconds=300,
        now=created_at + timedelta(hours=27),
    )

    assert fresh.ok is True
    assert stale.ok is False
    assert stale.reason == "verified_backup_stale"
    assert stale.cache_hit is True
    assert calls == 1


def test_archive_change_invalidates_full_verification_cache(tmp_path, monkeypatch):
    created_at = datetime(2026, 7, 31, 8, 0, 0, tzinfo=timezone.utc)
    backups, archive, _ = create_backup(monkeypatch, tmp_path, created_at=created_at)
    calls = 0
    original = freshness_module.verify_encrypted_backup

    def counted_verify(path):
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(freshness_module, "verify_encrypted_backup", counted_verify)
    first = backup_freshness_status(
        backup_dir=backups,
        required=True,
        now=created_at + timedelta(hours=1),
    )
    second = backup_freshness_status(
        backup_dir=backups,
        required=True,
        now=created_at + timedelta(hours=1, minutes=1),
    )
    os.utime(archive, None)
    third = backup_freshness_status(
        backup_dir=backups,
        required=True,
        now=created_at + timedelta(hours=1, minutes=2),
    )

    assert first.ok is True and first.cache_hit is False
    assert second.ok is True and second.cache_hit is True
    assert third.ok is True and third.cache_hit is False
    assert calls == 2


def test_revoked_backup_cannot_satisfy_readiness(tmp_path, monkeypatch):
    created_at = datetime(2026, 7, 31, 8, 0, 0, tzinfo=timezone.utc)
    backups, _, result = create_backup(monkeypatch, tmp_path, created_at=created_at)
    cutoff = datetime.fromisoformat(result.created_at) + timedelta(microseconds=1)
    with backup_maintenance_lock(backups):
        advance_backup_restore_fence(
            cutoff,
            reason="Case content cryptographic erasure",
            event_id="case:1:retention:1",
            backup_dir=backups,
        )

    status = backup_freshness_status(
        backup_dir=backups,
        required=True,
        now=created_at + timedelta(hours=1),
    )

    assert status.ok is False
    assert status.verified is False
    assert status.reason == "no_verified_restorable_backup"
    assert status.rejected_archives == 1


def test_tampered_backup_is_rejected_and_not_cached_as_verified(tmp_path, monkeypatch):
    created_at = datetime(2026, 7, 31, 8, 0, 0, tzinfo=timezone.utc)
    backups, archive, _ = create_backup(monkeypatch, tmp_path, created_at=created_at)
    payload = bytearray(archive.read_bytes())
    payload[-1] ^= 1
    archive.write_bytes(payload)

    status = backup_freshness_status(
        backup_dir=backups,
        required=True,
        now=created_at + timedelta(hours=1),
    )

    assert status.ok is False
    assert status.reason == "no_verified_restorable_backup"
    assert status.rejected_archives >= 1


def test_backup_from_unreasonable_future_does_not_satisfy_readiness(
    tmp_path,
    monkeypatch,
):
    current = datetime(2026, 7, 31, 8, 0, 0, tzinfo=timezone.utc)
    backups, _, _ = create_backup(
        monkeypatch,
        tmp_path,
        created_at=current + timedelta(minutes=10),
    )

    status = backup_freshness_status(
        backup_dir=backups,
        required=True,
        future_clock_skew_seconds=300,
        now=current,
    )

    assert status.ok is False
    assert status.reason == "no_verified_restorable_backup"
    assert status.rejected_archives == 1


def test_non_production_readiness_does_not_require_backup(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "test")

    status = backup_freshness_status(required=None)

    assert status.required is False
    assert status.ok is True
    assert status.reason == "not_required"


def test_ready_endpoint_exposes_and_enforces_backup_freshness(monkeypatch):
    unavailable = BackupFreshnessStatus(
        required=True,
        ok=False,
        verified=False,
        archive=None,
        created_at=None,
        age_seconds=None,
        max_age_seconds=26 * 3600,
        archives_seen=0,
        rejected_archives=0,
        cache_hit=False,
        reason="no_verified_restorable_backup",
    )
    monkeypatch.setattr(main_module, "backup_freshness_status", lambda: unavailable)

    response = TestClient(main_module.create_app()).get("/ready")
    payload = response.json()

    assert response.status_code == 200
    assert payload["checks"]["recent_verified_backup"] is False
    assert payload["backup_security"]["freshness"]["required"] is True
    assert payload["backup_security"]["freshness"]["reason"] == (
        "no_verified_restorable_backup"
    )
    assert payload["ok"] is False
