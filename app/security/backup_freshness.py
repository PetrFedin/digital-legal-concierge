from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings
from app.security.backup_encryption import (
    BACKUP_SUFFIX,
    BackupSecurityError,
    verify_encrypted_backup,
)
from app.security.backup_restore_fence import (
    FENCE_FILE_NAME,
    BackupRestoreFenceError,
    assert_backup_not_revoked,
    backup_maintenance_lock,
    read_backup_restore_fence,
)


class BackupFreshnessError(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedBackupSnapshot:
    archive: str | None
    created_at: datetime | None
    encrypted_size: int | None
    key_id: str | None
    archives_seen: int
    rejected_archives: int
    reason: str | None


@dataclass(frozen=True)
class BackupFreshnessStatus:
    required: bool
    ok: bool
    verified: bool
    archive: str | None
    created_at: str | None
    age_seconds: int | None
    max_age_seconds: int
    archives_seen: int
    rejected_archives: int
    cache_hit: bool
    reason: str | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class _CacheEntry:
    fingerprint: tuple[tuple[object, ...], ...]
    checked_monotonic: float
    snapshot: VerifiedBackupSnapshot


_CACHE: dict[str, _CacheEntry] = {}
_CACHE_LOCK = threading.RLock()


def clear_backup_freshness_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: object) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise BackupFreshnessError("В backup отсутствует created_at")
    try:
        return _utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except ValueError as error:
        raise BackupFreshnessError("Backup содержит некорректный created_at") from error


def _safe_root(backup_dir: str | Path | None = None) -> Path:
    root = Path(backup_dir or settings.backup_dir)
    if not root.exists():
        raise BackupFreshnessError("Каталог резервных копий отсутствует")
    if root.is_symlink() or not root.is_dir():
        raise BackupFreshnessError("Каталог резервных копий имеет небезопасный тип")
    return root.resolve()


def _stat_tuple(path: Path) -> tuple[object, ...]:
    stat = path.stat()
    return (path.name, stat.st_size, stat.st_mtime_ns, stat.st_ino)


def _fingerprint(root: Path) -> tuple[tuple[object, ...], ...]:
    entries: list[tuple[object, ...]] = []
    fence = root / FENCE_FILE_NAME
    if fence.exists():
        if fence.is_symlink() or not fence.is_file():
            entries.append((FENCE_FILE_NAME, "unsafe"))
        else:
            entries.append(("fence", *_stat_tuple(fence)))
    else:
        entries.append(("fence", "absent"))
    for path in sorted(root.glob(f"*{BACKUP_SUFFIX}")):
        if path.is_symlink() or not path.is_file():
            entries.append((path.name, "unsafe"))
            continue
        entries.append(("archive", *_stat_tuple(path)))
    return tuple(entries)


def _scan_verified_backup(
    root: Path,
    *,
    now: datetime,
    future_clock_skew_seconds: int,
) -> VerifiedBackupSnapshot:
    # Validate the signed fence once before evaluating any archive. A damaged
    # fence is a policy failure and must not be downgraded to "no backups".
    read_backup_restore_fence(root)

    paths = [
        path
        for path in root.glob(f"*{BACKUP_SUFFIX}")
        if path.is_file() and not path.is_symlink()
    ]
    candidates: list[tuple[datetime, Path]] = []
    rejected = 0
    future_limit = now + timedelta(seconds=future_clock_skew_seconds)

    for path in paths:
        try:
            metadata = assert_backup_not_revoked(path, backup_dir=root)
            created_at = _parse_timestamp(metadata.created_at)
            if created_at > future_limit:
                rejected += 1
                continue
            candidates.append((created_at, path))
        except BackupRestoreFenceError:
            raise
        except (BackupSecurityError, BackupFreshnessError, OSError):
            rejected += 1

    # Header metadata is only a prioritization hint. Each candidate is fully
    # decrypted and authenticated before its timestamp can satisfy readiness.
    candidates.sort(key=lambda item: item[0], reverse=True)
    for _, path in candidates:
        try:
            verified = verify_encrypted_backup(path)
            created_at = _parse_timestamp(verified.created_at)
            if created_at > future_limit:
                rejected += 1
                continue
            return VerifiedBackupSnapshot(
                archive=path.name,
                created_at=created_at,
                encrypted_size=path.stat().st_size,
                key_id=verified.key_id,
                archives_seen=len(paths),
                rejected_archives=rejected,
                reason=None,
            )
        except (BackupSecurityError, BackupFreshnessError, OSError):
            rejected += 1

    return VerifiedBackupSnapshot(
        archive=None,
        created_at=None,
        encrypted_size=None,
        key_id=None,
        archives_seen=len(paths),
        rejected_archives=rejected,
        reason="no_verified_restorable_backup",
    )


def _verified_snapshot(
    root: Path,
    *,
    now: datetime,
    cache_seconds: int,
    future_clock_skew_seconds: int,
) -> tuple[VerifiedBackupSnapshot, bool]:
    with backup_maintenance_lock(root):
        fingerprint = _fingerprint(root)
        key = str(root)
        current_monotonic = time.monotonic()
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
        if (
            cached is not None
            and cached.fingerprint == fingerprint
            and current_monotonic - cached.checked_monotonic <= cache_seconds
        ):
            return cached.snapshot, True

        snapshot = _scan_verified_backup(
            root,
            now=now,
            future_clock_skew_seconds=future_clock_skew_seconds,
        )
        entry = _CacheEntry(
            fingerprint=fingerprint,
            checked_monotonic=current_monotonic,
            snapshot=snapshot,
        )
        with _CACHE_LOCK:
            _CACHE[key] = entry
        return snapshot, False


def backup_freshness_status(
    *,
    backup_dir: str | Path | None = None,
    required: bool | None = None,
    max_age_hours: int | None = None,
    cache_seconds: int | None = None,
    future_clock_skew_seconds: int | None = None,
    now: datetime | None = None,
) -> BackupFreshnessStatus:
    required_now = (
        settings.app_env == "production"
        and settings.backup_readiness_required_in_production
        if required is None
        else bool(required)
    )
    maximum_hours = int(max_age_hours or settings.backup_max_age_hours)
    cache_ttl = int(
        settings.backup_freshness_cache_seconds
        if cache_seconds is None
        else cache_seconds
    )
    future_skew = int(
        settings.backup_future_clock_skew_seconds
        if future_clock_skew_seconds is None
        else future_clock_skew_seconds
    )
    max_age_seconds = maximum_hours * 3600

    if not required_now:
        return BackupFreshnessStatus(
            required=False,
            ok=True,
            verified=False,
            archive=None,
            created_at=None,
            age_seconds=None,
            max_age_seconds=max_age_seconds,
            archives_seen=0,
            rejected_archives=0,
            cache_hit=False,
            reason="not_required",
        )
    if maximum_hours < 1 or maximum_hours > 8760:
        return BackupFreshnessStatus(
            required=True,
            ok=False,
            verified=False,
            archive=None,
            created_at=None,
            age_seconds=None,
            max_age_seconds=max_age_seconds,
            archives_seen=0,
            rejected_archives=0,
            cache_hit=False,
            reason="invalid_max_age_configuration",
        )
    if cache_ttl < 0 or cache_ttl > 3600 or future_skew < 0 or future_skew > 3600:
        return BackupFreshnessStatus(
            required=True,
            ok=False,
            verified=False,
            archive=None,
            created_at=None,
            age_seconds=None,
            max_age_seconds=max_age_seconds,
            archives_seen=0,
            rejected_archives=0,
            cache_hit=False,
            reason="invalid_freshness_configuration",
        )

    current = _utc(now or datetime.now(timezone.utc))
    try:
        root = _safe_root(backup_dir)
        snapshot, cache_hit = _verified_snapshot(
            root,
            now=current,
            cache_seconds=cache_ttl,
            future_clock_skew_seconds=future_skew,
        )
    except BackupRestoreFenceError:
        return BackupFreshnessStatus(
            required=True,
            ok=False,
            verified=False,
            archive=None,
            created_at=None,
            age_seconds=None,
            max_age_seconds=max_age_seconds,
            archives_seen=0,
            rejected_archives=0,
            cache_hit=False,
            reason="restore_fence_invalid",
        )
    except (BackupFreshnessError, OSError):
        return BackupFreshnessStatus(
            required=True,
            ok=False,
            verified=False,
            archive=None,
            created_at=None,
            age_seconds=None,
            max_age_seconds=max_age_seconds,
            archives_seen=0,
            rejected_archives=0,
            cache_hit=False,
            reason="backup_directory_unavailable",
        )

    if snapshot.created_at is None:
        return BackupFreshnessStatus(
            required=True,
            ok=False,
            verified=False,
            archive=None,
            created_at=None,
            age_seconds=None,
            max_age_seconds=max_age_seconds,
            archives_seen=snapshot.archives_seen,
            rejected_archives=snapshot.rejected_archives,
            cache_hit=cache_hit,
            reason=snapshot.reason,
        )

    age_seconds = max(0, int((current - snapshot.created_at).total_seconds()))
    fresh = age_seconds <= max_age_seconds
    return BackupFreshnessStatus(
        required=True,
        ok=fresh,
        verified=True,
        archive=snapshot.archive,
        created_at=snapshot.created_at.isoformat(),
        age_seconds=age_seconds,
        max_age_seconds=max_age_seconds,
        archives_seen=snapshot.archives_seen,
        rejected_archives=snapshot.rejected_archives,
        cache_hit=cache_hit,
        reason=None if fresh else "verified_backup_stale",
    )


__all__ = [
    "BackupFreshnessError",
    "BackupFreshnessStatus",
    "backup_freshness_status",
    "clear_backup_freshness_cache",
]
