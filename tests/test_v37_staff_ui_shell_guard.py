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


def test_document_access_portal_shell_is_server_side_staff_guarded():
    guard = read("app/api/staff_ui_shell_guard.py")
    portal = read("app/api/document_access_portal.py")
    main = read("app/main.py")

    assert '@router.get("/document-access/ui"' in guard
    assert "DOCUMENT_ACCESS_HTML" in guard
    assert "return await _staff_shell(request, db, x_admin_token, DOCUMENT_ACCESS_HTML)" in guard
    assert "async def document_access_ui():" in portal
    assert "return HTMLResponse(DOCUMENT_ACCESS_HTML)" in portal
    assert main.index('(\"initial_setup_wizard\", initial_setup_wizard_router)') < main.index(
        '(\"document_access_portal\", document_access_portal_router)'
    )


def test_contract_center_shell_uses_guided_staff_recovery_before_legacy_route():
    guard = read("app/api/staff_ui_shell_guard.py")
    contract = read("app/api/contract_center.py")
    main = read("app/main.py")

    assert '@router.get("/contracts/ui"' in guard
    assert "CONTRACT_CENTER_HTML" in guard
    assert "return await _staff_shell(request, db, x_admin_token, CONTRACT_CENTER_HTML)" in guard
    assert '@router.get("/ui", response_class=HTMLResponse)' in contract
    assert "return HTMLResponse(CONTRACT_CENTER_HTML)" in contract
    assert main.index('(\"initial_setup_wizard\", initial_setup_wizard_router)') < main.index(
        '(\"contract_center\", contract_center_router)'
    )


def test_anonymous_lawyer_ui_goes_to_login_and_incomplete_staff_to_landing():
    guard = read("app/api/staff_ui_shell_guard.py")

    assert 'RedirectResponse(url="/login", status_code=303)' in guard
    assert 'RedirectResponse(url="/admin-ui", status_code=303)' in guard
    assert "error.status_code in {403, 409}" in guard


def test_guard_renders_full_composite_workspace_not_base_template():
    guard = read("app/api/staff_ui_shell_guard.py")
    contract = read("app/api/contract_workspace_ui.py")
    rejection = read("app/api/lawyer_workspace_rejection_ui.py")
    consultation = read("app/api/lawyer_consultation_decision_guard.py")

    assert "contract_aware_workspace_html" in guard
    assert "guarded_consultation_desk_html" in guard
    assert "return HTMLResponse(contract_aware_workspace_html())" in guard
    assert "return HTMLResponse(guarded_consultation_desk_html())" in guard

    # Composite workspace must still contain all guided business patches that
    # would otherwise be hidden by the early auth route's precedence.
    assert "_WORKSPACE_DEEP_LINK_PATCH" in rejection
    assert "_M1_REJECTION_PATCH" in rejection
    assert "_M1_POA_PATCH" in rejection
    assert "_COURT_DECISION_PATCH" in rejection
    assert "_CONTRACT_WORKSPACE_PATCH" in contract
    assert "_CONSULTATION_DRAFT_PATCH" in consultation
    assert "ALLOWED_COMPLETION_DECISIONS" in consultation


def test_staff_shell_guard_is_mounted_before_legacy_staff_routers():
    operator_guard = read("app/api/operator_guard.py")
    initial_setup = read("app/api/initial_setup_wizard.py")
    main = read("app/main.py")

    assert "router.include_router(staff_ui_shell_guard_router)" in operator_guard
    assert "router.include_router(operator_guard_router)" in initial_setup
    initial = main.index('(\"initial_setup_wizard\", initial_setup_wizard_router)')
    assert initial < main.index('(\"guided_lawyer_ui\", guided_lawyer_ui_router)')
    assert initial < main.index('(\"lawyer_workspace_rejection_ui\", lawyer_workspace_rejection_ui_router)')
    assert initial < main.index('(\"contract_workspace_ui\", contract_workspace_ui_router)')
    assert initial < main.index('(\"contract_center\", contract_center_router)')
    assert initial < main.index('(\"lawyer_workspace\", lawyer_workspace_router)')
    assert initial < main.index('(\"lawyer_consultation_desk\", lawyer_consultation_desk_router)')
    assert initial < main.index('(\"document_access_portal\", document_access_portal_router)')


def test_operator_hub_recovers_incomplete_or_unsupported_staff():
    guard = read("app/api/operator_guard.py")
    block = guard.split('@router.get("/operator"', 1)[1]

    assert "error.status_code in {403, 409}" in block
    assert 'url="/admin-ui"' in block
    assert "actor.role not in {ROLE_LAWYER, ROLE_ADMIN, ROLE_SUPERADMIN}" in block
