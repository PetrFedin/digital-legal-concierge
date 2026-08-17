from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_workdesk_case_id_query_is_consumed_and_opens_case():
    guard = read("app/api/workdesk_ui_guard.py")

    assert "new URLSearchParams(window.location.search).get('case_id')" in guard
    assert "await openCase(requested)" in guard
    assert "cv.scrollIntoView" in guard
    assert "WORKDESK_HTML.replace(marker, _WORKDESK_DEEP_LINK_BOOT, 1)" in guard


def test_workdesk_open_and_close_keep_url_in_sync():
    guard = read("app/api/workdesk_ui_guard.py")

    assert "url.searchParams.set('case_id',String(numeric))" in guard
    assert "url.searchParams.delete('case_id')" in guard
    assert "history.replaceState" in guard


def test_workdesk_ui_and_action_shells_are_server_side_admin_guarded():
    guard = read("app/api/workdesk_ui_guard.py")

    assert '@router.get("/admin/workdesk/ui"' in guard
    assert '"/admin/workdesk/cases/{case_id}/action/{task}"' in guard
    assert "resolve_document_actor" in guard
    assert "actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}" in guard
    assert 'RedirectResponse(url="/login", status_code=303)' in guard
    assert 'RedirectResponse(url="/admin-ui", status_code=303)' in guard


def test_workdesk_guard_is_mounted_before_legacy_workdesk_router():
    operator = read("app/api/operator_guard.py")
    initial_setup = read("app/api/initial_setup_wizard.py")
    main = read("app/main.py")

    assert "router.include_router(workdesk_ui_guard_router)" in operator
    assert "router.include_router(operator_guard_router)" in initial_setup
    assert main.index('(\"initial_setup_wizard\", initial_setup_wizard_router)') < main.index(
        '(\"workdesk\", workdesk_router)'
    )
