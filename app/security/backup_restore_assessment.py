from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.security.backup_encryption import (
    BackupMetadata,
    BackupSecurityError,
    inspect_encrypted_backup,
)
from app.security.backup_restore_fence import (
    BackupRestoreFenceError,
    assert_backup_not_revoked,
)


class BackupRevokedError(BackupSecurityError):
    """The archive is cryptographically valid but policy forbids restore."""


@dataclass(frozen=True)
class BackupRestoreAssessment:
    metadata: BackupMetadata
    revoked: bool
    restorable: bool
    block_reason: str | None


def assess_backup_restore(
    archive: str | Path,
    *,
    backup_dir: str | Path | None = None,
) -> BackupRestoreAssessment:
    """Classify a recognized archive without weakening the restore fence.

    The first header inspection distinguishes malformed archives from valid
    encrypted containers. The canonical fence check then decides whether that
    valid container is still restorable. A damaged or unverifiable fence is
    intentionally propagated and must block all restore operations.
    """

    metadata = inspect_encrypted_backup(archive)
    try:
        checked = assert_backup_not_revoked(archive, backup_dir=backup_dir)
    except BackupRestoreFenceError:
        raise
    except BackupSecurityError:
        return BackupRestoreAssessment(
            metadata=metadata,
            revoked=True,
            restorable=False,
            block_reason="revoked_by_cryptographic_erasure",
        )
    return BackupRestoreAssessment(
        metadata=checked,
        revoked=False,
        restorable=True,
        block_reason=None,
    )


def require_backup_restorable(
    archive: str | Path,
    *,
    backup_dir: str | Path | None = None,
) -> BackupMetadata:
    assessment = assess_backup_restore(archive, backup_dir=backup_dir)
    if assessment.revoked:
        raise BackupRevokedError(
            "Резервная копия отозвана политикой криптографического удаления"
        )
    return assessment.metadata


__all__ = [
    "BackupRestoreAssessment",
    "BackupRevokedError",
    "assess_backup_restore",
    "require_backup_restorable",
]
