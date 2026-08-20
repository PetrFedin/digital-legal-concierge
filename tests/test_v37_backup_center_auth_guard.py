from pathlib import Path

from app.api.backup_manager import (
    backup_status_override,
    backup_ui_override,
    verify_backup_override,
)
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _only(method: str, path: str):
    rows = [
        route
        for route in create_app().routes
        if getattr(route, "path", None) == path
        and method in (getattr(route, "methods", None) or set())
    ]
    assert len(rows) == 1, (method, path, [row.name for row in rows])
    return rows[0]


def test_backup_manager_is_the_single_hardened_backup_center_owner():
    assert _only("GET", "/backup-center/status").endpoint is backup_status_override
    assert _only("GET", "/backup-center/ui").endpoint is backup_ui_override
    assert (
        _only("POST", "/backup-center/verify/{archive_name}").endpoint
        is verify_backup_override
    )


def test_backup_center_legacy_facade_is_route_free():
    facade = read("app/api/backup_center.py")
    operator = read("app/api/operator_guard.py")

    assert "backup_center_impl" in facade
    assert "router = APIRouter" in facade
    assert "@router." not in facade
    assert "router.add_api_route" not in facade
    assert "backup_center_guard_router" not in operator


def test_backup_manager_requires_personal_superadmin_and_guides_anonymous_ui():
    manager = read("app/api/backup_manager.py")

    assert "resolve_document_actor" in manager
    assert "actor.role != ROLE_SUPERADMIN" in manager
    assert "await _require_superadmin(request, db, x_admin_token)" in manager
    assert 'RedirectResponse(url="/login", status_code=303)' in manager


def test_backup_verification_keeps_restore_fence_integrity_and_security_events():
    manager = read("app/api/backup_manager.py")

    assert "_verify_restorable_archive" in manager
    assert "BackupRevokedError" in manager
    assert "BackupRestoreFenceError" in manager
    assert "BackupSecurityError" in manager
    assert "record_security_event_best_effort" in manager
    assert 'result.pop("database_url_configured", None)' in manager
    assert 'result.pop("encryption_key_id", None)' in manager


def test_backup_alias_paths_are_unique_too():
    _only("GET", "/backup-manager/status")
    _only("GET", "/backup-manager/ui")
