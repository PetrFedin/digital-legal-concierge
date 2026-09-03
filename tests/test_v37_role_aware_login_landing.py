from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_non_superadmin_login_uses_canonical_staff_landing():
    auth = read("app/api/auth.py")

    assert 'RedirectResponse(url="/admin-ui", status_code=303)' in auth


def test_canonical_staff_landing_routes_admin_and_lawyer_by_role():
    guard = read("app/api/initial_setup_wizard.py")
    block = guard.split('@router.get("/admin-ui")', 1)[1].split(
        '@router.get("/initial-setup-wizard/status")', 1
    )[0]

    assert "ROLE_LAWYER" in guard
    assert "_staff_ui_or_login" in guard
    assert "resolve_document_actor" in guard
    assert "actor.role in {ROLE_ADMIN, ROLE_SUPERADMIN}" in block
    assert 'url="/admin/workdesk/ui"' in block
    assert "actor.role == ROLE_LAWYER" in block
    assert 'url="/lawyer/workspace/ui"' in block


def test_anonymous_staff_landing_recovers_to_login():
    guard = read("app/api/initial_setup_wizard.py")
    block = guard.split('@router.get("/admin-ui")', 1)[1].split(
        '@router.get("/initial-setup-wizard/status")', 1
    )[0]

    assert "if actor is None:" in block
    assert 'url="/login"' in block


def test_unsupported_or_incomplete_staff_profile_has_recovery_screen():
    guard = read("app/api/initial_setup_wizard.py")
    helper = guard.split("async def _staff_ui_or_login", 1)[1].split(
        '@router.get("/health")', 1
    )[0]
    block = guard.split('@router.get("/admin-ui")', 1)[1].split(
        '@router.get("/initial-setup-wizard/status")', 1
    )[0]

    assert 'error.reason == "role_denied"' in helper
    assert "StaffLandingProblem" in helper
    assert "error.status_code in {403, 409}" in helper
    assert "isinstance(actor, StaffLandingProblem)" in block
    assert "Нужно настроить рабочий доступ" in guard
    assert 'action="/logout"' in guard
    assert "не назначает права автоматически" in guard
