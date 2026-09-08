from pathlib import Path

from fastapi.routing import iter_route_contexts

from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _only(path: str):
    rows = [
        route
        for route in iter_route_contexts(create_app().routes)
        if route.path == path and "GET" in (route.methods or set())
    ]
    assert len(rows) == 1, (path, [row.name for row in rows])
    return rows[0]


def test_superadmin_compatibility_guard_is_route_free_and_not_runtime_mounted():
    guard = read("app/api/superadmin_ui_guards.py")
    operator = read("app/api/operator_guard.py")
    setup = read("app/api/initial_setup_wizard.py")
    acceptance = read("app/api/acceptance_center.py")
    main = read("app/main.py")

    assert "superadmin_ui_guards_impl" in guard
    assert "router = APIRouter" in guard
    assert "@router." not in guard
    assert "router.add_api_route" not in guard
    assert "superadmin_ui_guards_router" not in operator
    assert "superadmin_ui_guards_router" not in setup
    assert "superadmin_ui_guards_router" not in acceptance
    assert "superadmin_ui_guards" not in main


def test_access_management_ui_has_its_own_superadmin_gate():
    source = read("app/api/access_management.py")

    assert "async def _require_superadmin(" in source
    assert "actor.role != ROLE_SUPERADMIN" in source
    assert "async def _superadmin_ui_or_redirect(" in source
    assert 'RedirectResponse(url="/login", status_code=303)' in source
    _only("/access/ui")


def test_audit_and_security_ui_authenticate_before_returning_html():
    audit = read("app/api/audit_center.py")
    security = read("app/api/security_event_center.py")

    assert "await require_audit_superadmin(request, db, x_admin_token)" in audit
    assert "actor.role != ROLE_SUPERADMIN" in audit
    assert 'RedirectResponse(url="/login", status_code=303)' in audit
    assert "await require_security_superadmin(request, db, x_admin_token)" in security
    assert "actor.role != ROLE_SUPERADMIN" in security
    assert 'RedirectResponse(url="/login", status_code=303)' in security
    _only("/audit-center/ui")
    _only("/security-events/ui")


def test_retention_shell_is_fail_closed_by_global_personal_session_gate():
    retention = read("app/api/retention_center.py")
    session_guard = read("app/security/session_guard.py")

    assert "async def require_retention_superadmin(" in retention
    assert "actor.role != ROLE_SUPERADMIN" in retention
    assert '"/retention/ui": frozenset({ROLE_SUPERADMIN})' in session_guard
    assert "protected_ui_roles = _PROTECTED_UI_ROLES.get(path)" in session_guard
    assert "ROLE_SUPERADMIN in current_roles" in session_guard
    _only("/retention/ui")


def test_recovery_center_ui_and_mutations_require_superadmin():
    recovery = read("app/api/recovery_center.py")

    assert "async def _require_superadmin(" in recovery
    assert "actor.role != ROLE_SUPERADMIN" in recovery
    assert "await _require_superadmin(request, db, x_admin_token)" in recovery
    assert "MAX_RECOVERY_BATCH = 100" in recovery
    assert ".with_for_update()" in recovery
    _only("/recovery-center/ui")


def test_sensitive_superadmin_ui_paths_have_exactly_one_runtime_owner():
    for path in (
        "/access/ui",
        "/audit-center/ui",
        "/security-events/ui",
        "/retention/ui",
        "/recovery-center/ui",
    ):
        _only(path)
