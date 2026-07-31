from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings
from app.security.backup_encryption import (
    BACKUP_SUFFIX,
    BackupSecurityError,
    _decrypt_backup_stream,
)
from app.security.backup_restore_fence import (
    BackupRestoreFenceError,
    backup_maintenance_lock,
)


class BackupRetentionError(RuntimeError):
    pass


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _created_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise BackupRetentionError(
            "Зашифрованная резервная копия содержит некорректный created_at"
        ) from error
    return _as_utc(parsed)


def _safe_root(backup_dir: str | Path | None = None) -> Path:
    root = Path(backup_dir or settings.backup_dir)
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise BackupRetentionError("Каталог резервных копий имеет небезопасный тип")
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root.resolve()


def cleanup_authenticated_backups(
    backup_dir: str | Path | None = None,
    *,
    retention_days: int | None = None,
    now: datetime | None = None,
) -> int:
    """Remove expired backups using cryptographically authenticated creation time.

    Filesystem mtime is intentionally ignored because it is mutable. The whole
    encrypted stream is authenticated before ``created_at`` can influence a
    deletion decision. Unreadable, tampered, unsupported or key-retired files
    are retained for operator review instead of being deleted on weak evidence.
    """

    root = _safe_root(backup_dir)
    days = max(1, int(retention_days or settings.backup_retention_days))
    cutoff = _as_utc(now or datetime.now(timezone.utc)) - timedelta(days=days)
    removed = 0

    try:
        with backup_maintenance_lock(root):
            for path in sorted(root.glob(f"*{BACKUP_SUFFIX}")):
                if path.is_symlink() or not path.is_file():
                    continue
                try:
                    metadata = _decrypt_backup_stream(path)
                    created_at = _created_at(metadata.created_at)
                except (BackupSecurityError, BackupRetentionError):
                    continue
                if created_at < cutoff:
                    path.unlink(missing_ok=True)
                    removed += 1

            if removed:
                try:
                    descriptor = os.open(root, os.O_RDONLY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                except OSError:
                    pass
    except BackupRestoreFenceError as error:
        raise BackupRetentionError(
            "Не удалось безопасно синхронизировать очистку резервных копий"
        ) from error

    return removed


__all__ = [
    "BackupRetentionError",
    "cleanup_authenticated_backups",
]
