from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_superadmin_guard_protects_sensitive_html_shells():
    guard = read("app/api/superadmin_ui_guards.py")

    assert '@router.get("/audit-center/ui"' in guard
    assert '@router.get("/security-events/ui"' in guard
    assert '@router.get("/retention/ui"' in guard
    assert "AUDIT_CENTER_HTML" in guard
    assert "SECURITY_EVENT_HTML" in guard
    assert "RETENTION_HTML" in guard
    assert "resolve_document_actor" in guard
    assert "actor.role != ROLE_SUPERADMIN" in guard


def test_superadmin_shells_recover_anonymous_and_role_mismatch():
    guard = read("app/api/superadmin_ui_guards.py")

    assert 'RedirectResponse(url="/login", status_code=303)' in guard
    assert 'RedirectResponse(url="/admin-ui", status_code=303)' in guard
    assert "error.status_code in {403, 409}" in guard
    assert "except DocumentAccessError as error:" in guard
    assert "except HTTPException as error:" in guard


def test_superadmin_guards_are_mounted_before_sensitive_legacy_centers():
    operator = read("app/api/operator_guard.py")
    initial_setup = read("app/api/initial_setup_wizard.py")
    main = read("app/main.py")

    assert "router.include_router(superadmin_ui_guards_router)" in operator
    assert "router.include_router(operator_guard_router)" in initial_setup
    initial = main.index('(\"initial_setup_wizard\", initial_setup_wizard_router)')
    assert initial < main.index('(\"audit_center\", audit_center_router)')
    assert initial < main.index('(\"security_event_center\", security_event_center_router)')
    assert initial < main.index('(\"retention_center\", retention_center_router)')


def test_retention_ui_no_longer_relies_only_on_client_side_auth():
    retention = read("app/api/retention_center.py")
    guard = read("app/api/superadmin_ui_guards.py")

    # Historical route returns the shell directly. The early exact guard must
    # therefore remain mounted ahead of retention_center.
    assert "async def retention_ui():" in retention
    assert "return HTMLResponse(RETENTION_HTML)" in retention
    assert '@router.get("/retention/ui"' in guard
