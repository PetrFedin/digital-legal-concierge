from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - Linux production uses flock.
    fcntl = None

from app.config import settings
from app.security.keyring import KeyEntry, backup_encryption_ring

FENCE_FILE_NAME = ".restore-fence.json"
LOCK_FILE_NAME = ".backup-maintenance.lock"
FENCE_VERSION = 1
MAX_FENCE_BYTES = 64 * 1024
_CONTEXT = b"digital-legal-concierge/backup-restore-fence/v1"
_FALLBACK_LOCK = threading.RLock()


class BackupRestoreFenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupRestoreFence:
    version: int
    cutoff_at: datetime
    key_id: str
    reason: str
    event_id: str
    signature: str

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "cutoff_at": self.cutoff_at.isoformat(),
            "key_id": self.key_id,
            "reason": self.reason,
            "event_id": self.event_id,
            "signature": self.signature,
        }


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_utc(value: object) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise BackupRestoreFenceError("В restore-fence отсутствует cutoff_at")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise BackupRestoreFenceError("Некорректный cutoff_at restore-fence") from error
    return _utc(parsed)


def _safe_backup_root(backup_dir: str | Path | None = None) -> Path:
    root = Path(backup_dir or settings.backup_dir)
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise BackupRestoreFenceError("Каталог резервных копий имеет небезопасный тип")
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root.resolve()


def _fence_path(backup_dir: str | Path | None = None) -> Path:
    return _safe_backup_root(backup_dir) / FENCE_FILE_NAME


def _canonical_payload(
    *,
    version: int,
    cutoff_at: datetime,
    key_id: str,
    reason: str,
    event_id: str,
) -> bytes:
    return json.dumps(
        {
            "version": int(version),
            "cutoff_at": _utc(cutoff_at).isoformat(),
            "key_id": str(key_id),
            "reason": str(reason),
            "event_id": str(event_id),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _signature(entry: KeyEntry, payload: bytes) -> str:
    domain_key = hmac.new(
        entry.secret.encode("utf-8"),
        _CONTEXT,
        hashlib.sha256,
    ).digest()
    return hmac.new(domain_key, payload, hashlib.sha256).hexdigest()


def _normalise_text(value: str, *, field: str, maximum: int) -> str:
    result = " ".join(str(value or "").split())
    if not result:
        raise BackupRestoreFenceError(f"Поле {field} restore-fence обязательно")
    return result[:maximum]


def read_backup_restore_fence(
    backup_dir: str | Path | None = None,
) -> BackupRestoreFence | None:
    path = _fence_path(backup_dir)
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise BackupRestoreFenceError("Restore-fence имеет небезопасный тип")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise BackupRestoreFenceError("Restore-fence не читается") from error
    if not raw or len(raw) > MAX_FENCE_BYTES:
        raise BackupRestoreFenceError("Restore-fence имеет некорректный размер")
    try:
        data = json.loads(raw.decode("utf-8"))
        version = int(data["version"])
        cutoff_at = _parse_utc(data["cutoff_at"])
        key_id = str(data["key_id"])
        reason = str(data["reason"])
        event_id = str(data["event_id"])
        signature = str(data["signature"])
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BackupRestoreFenceError("Restore-fence повреждён") from error
    if version != FENCE_VERSION:
        raise BackupRestoreFenceError("Версия restore-fence не поддерживается")
    entry = backup_encryption_ring().by_id(key_id)
    if entry is None:
        raise BackupRestoreFenceError(
            f"Ключ проверки restore-fence {key_id} отсутствует"
        )
    payload = _canonical_payload(
        version=version,
        cutoff_at=cutoff_at,
        key_id=key_id,
        reason=reason,
        event_id=event_id,
    )
    expected = _signature(entry, payload)
    if len(signature) != 64 or not hmac.compare_digest(signature, expected):
        raise BackupRestoreFenceError("Подпись restore-fence недействительна")
    return BackupRestoreFence(
        version=version,
        cutoff_at=cutoff_at,
        key_id=key_id,
        reason=reason,
        event_id=event_id,
        signature=signature,
    )


def _write_fence(
    fence: BackupRestoreFence,
    backup_dir: str | Path | None = None,
) -> None:
    root = _safe_backup_root(backup_dir)
    path = root / FENCE_FILE_NAME
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise BackupRestoreFenceError("Restore-fence имеет небезопасный тип")
    encoded = (
        json.dumps(
            fence.as_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{FENCE_FILE_NAME}.",
        suffix=".tmp",
        dir=root,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
        try:
            directory_descriptor = os.open(root, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError:
            pass
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


class BackupMaintenanceLock:
    """Cross-process exclusion for backup, restore and key-erasure maintenance."""

    def __init__(self, backup_dir: str | Path | None = None):
        self.backup_dir = backup_dir
        self._descriptor: int | None = None
        self._fallback_acquired = False

    def acquire(self) -> None:
        if self._descriptor is not None or self._fallback_acquired:
            raise BackupRestoreFenceError("Backup maintenance lock уже захвачен")
        root = _safe_backup_root(self.backup_dir)
        path = root / LOCK_FILE_NAME
        if path.exists() and path.is_symlink():
            raise BackupRestoreFenceError("Backup maintenance lock является symlink")
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                self._descriptor = descriptor
            else:  # pragma: no cover
                _FALLBACK_LOCK.acquire()
                self._fallback_acquired = True
                self._descriptor = descriptor
        except Exception:
            os.close(descriptor)
            raise

    def release(self) -> None:
        descriptor = self._descriptor
        self._descriptor = None
        try:
            if descriptor is not None and fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if self._fallback_acquired:  # pragma: no cover
                self._fallback_acquired = False
                _FALLBACK_LOCK.release()

    def __enter__(self) -> BackupMaintenanceLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


@contextmanager
def backup_maintenance_lock(
    backup_dir: str | Path | None = None,
) -> Iterator[BackupMaintenanceLock]:
    lock = BackupMaintenanceLock(backup_dir)
    lock.acquire()
    try:
        yield lock
    finally:
        lock.release()


def advance_backup_restore_fence(
    cutoff_at: datetime,
    *,
    reason: str,
    event_id: str,
    backup_dir: str | Path | None = None,
) -> BackupRestoreFence:
    cutoff = _utc(cutoff_at)
    safe_reason = _normalise_text(reason, field="reason", maximum=500)
    safe_event_id = _normalise_text(event_id, field="event_id", maximum=200)
    current = read_backup_restore_fence(backup_dir)
    active = backup_encryption_ring().require_active()
    if current is not None and current.cutoff_at > cutoff:
        cutoff = current.cutoff_at
        safe_reason = current.reason
        safe_event_id = current.event_id
    if (
        current is not None
        and current.cutoff_at == cutoff
        and current.key_id == active.key_id
    ):
        return current
    payload = _canonical_payload(
        version=FENCE_VERSION,
        cutoff_at=cutoff,
        key_id=active.key_id,
        reason=safe_reason,
        event_id=safe_event_id,
    )
    fence = BackupRestoreFence(
        version=FENCE_VERSION,
        cutoff_at=cutoff,
        key_id=active.key_id,
        reason=safe_reason,
        event_id=safe_event_id,
        signature=_signature(active, payload),
    )
    _write_fence(fence, backup_dir)
    return fence


def assert_backup_not_revoked(
    archive: str | Path,
    *,
    backup_dir: str | Path | None = None,
):
    from app.security.backup_encryption import (
        BackupSecurityError,
        inspect_encrypted_backup,
    )

    fence = read_backup_restore_fence(backup_dir)
    metadata = inspect_encrypted_backup(archive)
    if fence is None:
        return metadata
    created_at = _parse_utc(metadata.created_at)
    if created_at <= fence.cutoff_at:
        raise BackupSecurityError(
            "Резервная копия отозвана политикой криптографического удаления и "
            "не может быть проверена или восстановлена"
        )
    return metadata


def purge_revoked_backups(
    backup_dir: str | Path | None = None,
) -> int:
    from app.security.backup_encryption import BACKUP_SUFFIX, inspect_encrypted_backup

    root = _safe_backup_root(backup_dir)
    fence = read_backup_restore_fence(root)
    if fence is None:
        return 0
    removed = 0
    for path in root.glob(f"*{BACKUP_SUFFIX}"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            metadata = inspect_encrypted_backup(path)
            created_at = _parse_utc(metadata.created_at)
        except Exception:
            continue
        if created_at <= fence.cutoff_at:
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
    return removed


__all__ = [
    "BackupMaintenanceLock",
    "BackupRestoreFence",
    "BackupRestoreFenceError",
    "advance_backup_restore_fence",
    "assert_backup_not_revoked",
    "backup_maintenance_lock",
    "purge_revoked_backups",
    "read_backup_restore_fence",
]
