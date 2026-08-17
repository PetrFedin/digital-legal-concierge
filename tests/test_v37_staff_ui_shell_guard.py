from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_lawyer_ui_shells_are_server_side_authenticated():
    guard = read("app/api/staff_ui_shell_guard.py")

    assert '@router.get("/lawyer/workspace/ui"' in guard
    assert '@router.get("/lawyer/consultation-desk/ui"' in guard
    assert "resolve_document_actor" in guard
    assert "settings.admin_session_cookie" in guard
    assert "allowed_roles=frozenset({ROLE_LAWYER})" in guard


def test_shared_schedule_shell_requires_a_usable_staff_identity():
    guard = read("app/api/staff_ui_shell_guard.py")
    main = read("app/main.py")

    assert '@router.get("/consultation-slots/ui"' in guard
    assert "SLOTS_HTML" in guard
    assert "allowed_roles=frozenset({ROLE_LAWYER, ROLE_ADMIN, ROLE_SUPERADMIN})" in guard
    assert main.index('(\"initial_setup_wizard\", initial_setup_wizard_router)') < main.index(
        '(\"consultation_slots\", consultation_slots_router)'
    )


def test_anonymous_lawyer_ui_goes_to_login_and_incomplete_staff_to_landing():
    guard = read("app/api/staff_ui_shell_guard.py")

    assert 'RedirectResponse(url="/login", status_code=303)' in guard
    assert 'RedirectResponse(url="/admin-ui", status_code=303)' in guard
    assert "error.status_code in {403, 409}" in guard


def test_guard_reuses_guided_workspace_and_consultation_patches():
    guard = read("app/api/staff_ui_shell_guard.py")

    assert "WORKSPACE_HTML" in guard
    assert "CONSULTATION_DESK_HTML" in guard
    assert "_WORKSPACE_DEEP_LINK_PATCH" in guard
    assert "_CONSULTATION_DRAFT_PATCH" in guard
    assert "_inject_patch" in guard


def test_staff_shell_guard_is_mounted_before_legacy_staff_routers():
    operator_guard = read("app/api/operator_guard.py")
    initial_setup = read("app/api/initial_setup_wizard.py")
    main = read("app/main.py")

    assert "router.include_router(staff_ui_shell_guard_router)" in operator_guard
    assert "router.include_router(operator_guard_router)" in initial_setup
    assert main.index('(\"initial_setup_wizard\", initial_setup_wizard_router)') < main.index(
        '(\"guided_lawyer_ui\", guided_lawyer_ui_router)'
    )


def test_operator_hub_recovers_incomplete_or_unsupported_staff():
    guard = read("app/api/operator_guard.py")
    block = guard.split('@router.get("/operator"', 1)[1]

    assert "error.status_code in {403, 409}" in block
    assert 'url="/admin-ui"' in block
    assert "actor.role not in {ROLE_LAWYER, ROLE_ADMIN, ROLE_SUPERADMIN}" in block
