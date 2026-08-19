from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_staff_ui_helpers_are_not_shadow_route_owners():
    guard = read("app/api/staff_ui_guards.py")
    operator = read("app/api/operator_guard.py")

    assert "async def protected_payment_review_ui(" in guard
    assert "async def protected_sla_ui(" in guard
    assert "async def protected_document_review_ui(" in guard
    assert '@router.get("/admin/payment-reviews/ui"' not in guard
    assert '@router.get("/admin/sla/ui"' not in guard
    assert '@router.get("/document-access/review/ui"' not in guard
    assert "staff_ui_guards_router" not in operator


def test_staff_ui_gate_redirects_anonymous_and_role_mismatch_without_raw_json_dead_end():
    guard = read("app/api/staff_ui_guards.py")
    block = guard.split("async def _staff_gate", 1)[1].split("async def _guarded_html", 1)[0]

    assert 'RedirectResponse(url="/login", status_code=303)' in block
    assert 'RedirectResponse(url="/admin-ui", status_code=303)' in block
    assert "except DocumentAccessError" in block
    assert "except HTTPException" in block
    assert "error.status_code in {403, 409}" in block


def test_protected_staff_shells_are_registered_by_explicit_product_routers():
    guard = read("app/api/staff_ui_guards.py")
    payment_product = read("app/api/payment_review_product.py")
    sla_product = read("app/api/sla_product.py")
    document_product = read("app/api/document_access_product.py")
    main = read("app/main.py")

    assert "protected_payment_review_ui" in payment_product
    assert '"/ui"' in payment_product
    assert "protected_sla_ui" in sla_product
    assert '"/ui"' in sla_product
    assert "protected_document_review_ui" in document_product
    assert '"/review/ui"' in document_product
    assert '("payment_review_product", payment_review_product_router)' in main
    assert '("sla_product", sla_product_router)' in main
    assert '("document_access_product", document_access_product_router)' in main
    assert "gate = await _staff_gate(request, db, x_admin_token)" in guard
    assert "staff=True" in guard
