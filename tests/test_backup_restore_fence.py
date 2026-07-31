from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.domain.retention import CaseRetentionService
from app.domain.retention.backup_aware_case_retention_service import (
    BackupAwareCaseRetentionService,
)
from app.domain.retention.case_retention_service import (
    STATUS_APPROVED,
    CaseRetentionService as DirectCaseRetentionService,
)
from app.security.backup_encryption import (
    BackupSecurityError,
    encrypt_backup_payload,
)
from app.security.backup_restore_fence import (
    FENCE_FILE_NAME,
    BackupRestoreFenceError,
    advance_backup_restore_fence,
    assert_backup_not_revoked,
    backup_maintenance_lock,
    purge_revoked_backups,
    read_backup_restore_fence,
)

BACKUP_KEY_OLD = "backup-fence-old-" + "a" * 40
BACKUP_KEY_NEW = "backup-fence-new-" + "b" * 40


def configure(monkeypatch, tmp_path: Path) -> Path:
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "allow_legacy_security_key_fallback", False)
    monkeypatch.setattr(settings, "backup_encryption_key_id", "backups-old")
    monkeypatch.setattr(settings, "backup_encryption_key", BACKUP_KEY_OLD)
    monkeypatch.setattr(settings, "backup_encryption_previous_keys", "")
    monkeypatch.setattr(settings, "backup_dir", str(backup_dir))
    monkeypatch.setattr(settings, "max_backup_mb", 20)
    return backup_dir


def make_archive(tmp_path: Path, name: str):
    payload = tmp_path / f"{name}.payload"
    payload.write_bytes(f"payload:{name}".encode("utf-8"))
    archive = Path(settings.backup_dir) / f"{name}.dlcbak"
    metadata = encrypt_backup_payload(payload, archive)
    return archive, metadata


def test_old_archive_is_purged_and_cannot_be_reintroduced(tmp_path, monkeypatch):
    backup_dir = configure(monkeypatch, tmp_path)
    old_archive, old_metadata = make_archive(tmp_path, "old")
    saved_bytes = old_archive.read_bytes()
    old_created_at = datetime.fromisoformat(old_metadata.created_at)
    cutoff = old_created_at + timedelta(microseconds=1)

    with backup_maintenance_lock(backup_dir):
        fence = advance_backup_restore_fence(
            cutoff,
            reason="Cryptographic erasure test",
            event_id="case:1:retention:1",
            backup_dir=backup_dir,
        )
        assert purge_revoked_backups(backup_dir) == 1

    assert fence.cutoff_at == cutoff
    assert not old_archive.exists()

    # Returning a deleted archive or changing its mtime does not make it valid:
    # revocation uses authenticated created_at from the encrypted header.
    old_archive.write_bytes(saved_bytes)
    old_archive.touch()
    with pytest.raises(BackupSecurityError, match="отозвана"):
        assert_backup_not_revoked(old_archive, backup_dir=backup_dir)

    new_archive, _ = make_archive(tmp_path, "new")
    assert assert_backup_not_revoked(
        new_archive,
        backup_dir=backup_dir,
    ).key_id == "backups-old"


def test_fence_is_monotonic_and_tamper_evident(tmp_path, monkeypatch):
    backup_dir = configure(monkeypatch, tmp_path)
    later = datetime.now(timezone.utc) + timedelta(minutes=5)
    earlier = later - timedelta(hours=1)

    with backup_maintenance_lock(backup_dir):
        first = advance_backup_restore_fence(
            later,
            reason="Later deletion",
            event_id="case:2:retention:2",
            backup_dir=backup_dir,
        )
        second = advance_backup_restore_fence(
            earlier,
            reason="Attempted rollback",
            event_id="case:1:retention:1",
            backup_dir=backup_dir,
        )

    assert first.cutoff_at == later
    assert second.cutoff_at == later
    assert second.event_id == first.event_id

    fence_path = backup_dir / FENCE_FILE_NAME
    payload = json.loads(fence_path.read_text(encoding="utf-8"))
    payload["cutoff_at"] = earlier.isoformat()
    fence_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BackupRestoreFenceError, match="Подпись"):
        read_backup_restore_fence(backup_dir)


def test_fence_is_resigned_during_backup_key_rotation(tmp_path, monkeypatch):
    backup_dir = configure(monkeypatch, tmp_path)
    cutoff = datetime.now(timezone.utc)
    with backup_maintenance_lock(backup_dir):
        original = advance_backup_restore_fence(
            cutoff,
            reason="Initial erasure",
            event_id="case:3:retention:3",
            backup_dir=backup_dir,
        )
    assert original.key_id == "backups-old"

    monkeypatch.setattr(settings, "backup_encryption_key_id", "backups-new")
    monkeypatch.setattr(settings, "backup_encryption_key", BACKUP_KEY_NEW)
    monkeypatch.setattr(
        settings,
        "backup_encryption_previous_keys",
        f"backups-old:{BACKUP_KEY_OLD}",
    )
    with backup_maintenance_lock(backup_dir):
        rotated = advance_backup_restore_fence(
            cutoff,
            reason="Initial erasure",
            event_id="case:3:retention:3",
            backup_dir=backup_dir,
        )

    assert rotated.cutoff_at == cutoff
    assert rotated.key_id == "backups-new"
    assert read_backup_restore_fence(backup_dir).key_id == "backups-new"


def test_direct_retention_import_is_replaced_with_backup_aware_service():
    assert CaseRetentionService is BackupAwareCaseRetentionService
    assert DirectCaseRetentionService is BackupAwareCaseRetentionService


@pytest.mark.asyncio
async def test_retention_prepares_fence_before_base_execution(tmp_path, monkeypatch):
    backup_dir = configure(monkeypatch, tmp_path)
    record = SimpleNamespace(
        id=41,
        case_id=7,
        status=STATUS_APPROVED,
        executed_at=None,
    )
    case = SimpleNamespace(id=7, content_deleted_at=None)
    service = BackupAwareCaseRetentionService(None)
    monkeypatch.setattr(service, "_load_record", AsyncMock(return_value=record))
    monkeypatch.setattr(service, "_load_case", AsyncMock(return_value=case))

    async def fake_base_execute(self, *, record_id, actor_id, now=None):
        fence = read_backup_restore_fence(backup_dir)
        assert fence is not None
        record.executed_at = datetime.now(timezone.utc)
        return record

    from app.domain.retention import case_retention_service as base_module

    monkeypatch.setattr(
        base_module.CaseRetentionService.__mro__[1],
        "execute_deletion",
        fake_base_execute,
    )

    result = await service.execute_deletion(record_id=record.id, actor_id=99)
    assert result is record
    assert read_backup_restore_fence(backup_dir) is not None
