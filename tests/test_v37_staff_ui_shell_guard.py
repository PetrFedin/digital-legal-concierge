from pathlib import Path

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


def test_staff_shell_compatibility_router_is_not_part_of_runtime_assembly():
    operator_guard = read("app/api/operator_guard.py")
    setup = read("app/api/initial_setup_wizard.py")
    main = read("app/main.py")

    assert "staff_ui_shell_guard_router" not in operator_guard
    assert "operator_guard_router" not in setup
    assert "staff_ui_shell_guard" not in main


def test_consultation_schedule_ui_authenticates_on_canonical_handler():
    source = read("app/api/consultation_slots.py")
    block = source.split('@router.get("/ui"', 1)[1]

    assert "await _staff(request, db, x_admin_token)" in block
    assert "except DocumentAccessError as error:" in block
    assert 'RedirectResponse(url="/login?next=/consultation-slots/ui"' in block
    _only("GET", "/consultation-slots/ui")


def test_document_portal_is_server_guarded_without_shadow_route():
    portal = read("app/api/document_access_portal.py")
    session_guard = read("app/security/session_guard.py")

    assert "async def document_access_ui():" in portal
    assert '"/document-access/ui": frozenset({ROLE_ADMIN, ROLE_SUPERADMIN, ROLE_LAWYER})' in session_guard
    assert "protected_ui_roles = _PROTECTED_UI_ROLES.get(path)" in session_guard
    assert "if not token:" in session_guard
    _only("GET", "/document-access/ui")


def test_contract_center_ui_authenticates_on_canonical_handler():
    source = read("app/api/contract_center.py")
    block = source.split('@router.get("/ui"', 1)[1]

    assert "await _actor(request, db, x_admin_token)" in block
    assert 'RedirectResponse(url="/login", status_code=303)' in block
    _only("GET", "/contracts/ui")


def test_lawyer_workspaces_and_m2_mutations_have_one_product_owner():
    product = read("app/api/lawyer_product.py")
    base = read("app/api/lawyer.py")

    assert '"/lawyer/ui"' in product
    assert '"/lawyer/workspace/ui"' in product
    assert '"/lawyer/consultation-desk/ui"' in product
    assert '"/lawyer/consultations/{consultation_id}/complete"' in product
    assert '"/lawyer/consultations/{consultation_id}/client-no-show"' in product
    assert '"/ui"' not in base
    assert '"/consultations/{consultation_id}/complete"' not in base
    assert '"/consultations/{consultation_id}/client-no-show"' not in base

    for method, path in (
        ("GET", "/lawyer/ui"),
        ("GET", "/lawyer/workspace/ui"),
        ("GET", "/lawyer/consultation-desk/ui"),
        ("POST", "/lawyer/consultations/{consultation_id}/complete"),
        ("POST", "/lawyer/consultations/{consultation_id}/client-no-show"),
    ):
        _only(method, path)


def test_admin_payment_refund_sla_shells_are_product_owned_not_shell_guarded():
    payment = read("app/api/payment_review_product.py")
    refund = read("app/api/refund_product.py")
    sla = read("app/api/sla_product.py")

    assert "protected_payment_review_ui" in payment
    assert "refund_ui_guard" in refund
    assert "protected_sla_ui" in sla

    for path in (
        "/admin/payment-reviews/ui",
        "/admin/refunds/ui",
        "/admin/sla/ui",
    ):
        _only("GET", path)


def test_operator_is_canonical_authenticated_staff_hub():
    operator = read("app/api/operator.py")
    guard = read("app/api/operator_guard.py")

    assert '@router.get("/operator"' in operator
    assert "resolve_document_actor" in operator
    assert "@router." not in guard
    assert "router.add_api_route" not in guard
    _only("GET", "/operator")
