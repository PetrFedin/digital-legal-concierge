from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_workdesk_case_id_query_is_consumed_and_opens_case():
    runtime_ui = read("app/api/workdesk_runtime_ui.py")

    assert "new URLSearchParams(window.location.search).get('case_id')" in runtime_ui
    assert "void openCase(requestedCaseId)" in runtime_ui
    assert "cv.scrollIntoView" in runtime_ui
    assert "_append_body_extensions" in runtime_ui
    assert "WORKDESK_HTML.replace(" not in runtime_ui


def test_workdesk_open_and_close_keep_url_in_sync():
    runtime_ui = read("app/api/workdesk_runtime_ui.py")

    assert "url.searchParams.set('case_id',String(id))" in runtime_ui
    assert "url.searchParams.delete('case_id')" in runtime_ui
    assert "history.replaceState" in runtime_ui


def test_workdesk_ui_and_action_shells_are_server_side_admin_guarded():
    runtime_ui = read("app/api/workdesk_runtime_ui.py")
    product = read("app/api/workdesk_product.py")
    projections = read("app/api/workdesk_projections.py")

    assert "async def workdesk_runtime_ui(" in runtime_ui
    assert "resolve_document_actor" in runtime_ui
    assert "actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}" in runtime_ui
    assert 'RedirectResponse(url="/login", status_code=303)' in runtime_ui
    assert 'RedirectResponse(url="/admin-ui", status_code=303)' in runtime_ui
    assert '"/admin/workdesk/ui"' in product
    assert '"/admin/workdesk/cases/{case_id}/action/{task}"' in product
    assert "guarded_workdesk_case_action" in product
    assert "resolve_document_actor" in projections
    assert "async def guarded_workdesk_case_action(" in projections


def test_workdesk_has_product_owner_not_guard_precedence():
    operator = read("app/api/operator_guard.py")
    main = read("app/main.py")
    product = read("app/api/workdesk_product.py")

    assert "workdesk_ui_guard_router" not in operator
    assert "workdesk_router" not in main
    assert '("workdesk_product", workdesk_product_router)' in main
    assert "workdesk_runtime_ui" in product
