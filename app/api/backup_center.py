"""Route-free facade for the historical backup-center implementation.

The backup implementation remains available to ``backup_manager`` and existing
imports, but runtime ownership of ``/backup-center/*`` belongs exclusively to
``app.api.backup_manager``. No public URL or backup business rule changes here.
"""

from fastapi import APIRouter

from app.api import backup_center_impl as _impl

# Re-export implementation helpers used by the canonical backup manager and by
# operational tests. The old decorated router intentionally stays inside the
# unmounted implementation module.
ARCHIVE_NAME_RE = _impl.ARCHIVE_NAME_RE
BACKUP_CENTER_HTML = _impl.BACKUP_CENTER_HTML
BackupSecurityError = _impl.BackupSecurityError
_safe_archive = _impl._safe_archive
_safe_backup_root = _impl._safe_backup_root
_verify_restorable_archive = _impl._verify_restorable_archive
backup_inventory = _impl.backup_inventory
backup_center_status = _impl.backup_center_status
backup_center_ui = _impl.backup_center_ui
verify_backup_archive = _impl.verify_backup_archive

router = APIRouter(tags=["backup-center-retired"])


def __getattr__(name: str):
    """Keep historical non-router imports working during consolidation."""

    if name == "router":
        return router
    return getattr(_impl, name)


__all__ = [
    "ARCHIVE_NAME_RE",
    "BACKUP_CENTER_HTML",
    "_safe_archive",
    "_safe_backup_root",
    "_verify_restorable_archive",
    "backup_inventory",
    "backup_center_status",
    "backup_center_ui",
    "verify_backup_archive",
    "router",
]
