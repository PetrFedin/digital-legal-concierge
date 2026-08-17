from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_existing_staff_ui_guards_are_mounted_in_early_operator_layer():
    operator = read("app/api/operator_guard.py")
    initial_setup = read("app/api/initial_setup_wizard.py")
    main = read("app/main.py")

    assert "from app.api.staff_ui_guards import router as staff_ui_guards_router" in operator
    assert "router.include_router(staff_ui_guards_router)" in operator
    assert "router.include_router(operator_guard_router)" in initial_setup
    assert main.index('(\"initial_setup_wizard\", initial_setup_wizard_router)') < main.index(
        '(\"payment_review_center\", payment_review_center_router)'
    )
    assert main.index('(\"initial_setup_wizard\", initial_setup_wizard_router)') < main.index(
        '(\"sla_center\", sla_center_router)'
    )
    assert main.index('(\"initial_setup_wizard\", initial_setup_wizard_router)') < main.index(
        '(\"document_access\", document_access_router)'
    )


def test_staff_ui_gate_redirects_anonymous_and_role_mismatch_without_raw_json_dead_end():
    guard = read("app/api/staff_ui_guards.py")
    block = guard.split("async def _staff_gate", 1)[1].split("async def _guarded_html", 1)[0]

    assert 'RedirectResponse(url="/login", status_code=303)' in block
    assert 'RedirectResponse(url="/admin-ui", status_code=303)' in block
    assert "except DocumentAccessError" in block
    assert "except HTTPException" in block
    assert "error.status_code in {403, 409}" in block


def test_protected_staff_shells_use_common_gate():
    guard = read("app/api/staff_ui_guards.py")

    assert '@router.get("/admin/payment-reviews/ui"' in guard
    assert '@router.get("/admin/sla/ui"' in guard
    assert '@router.get("/document-access/review/ui"' in guard
    assert "gate = await _staff_gate(request, db, x_admin_token)" in guard
    assert "staff=True" in guard
