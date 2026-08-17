from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _assert_ui_recovery(path: str, function_name: str) -> None:
    source = read(path)
    block = source.split(f"async def {function_name}", 1)[1]
    assert "except DocumentAccessError as error:" in block
    assert "except HTTPException as error:" in block
    assert "error.status_code in {403, 409}" in block
    assert 'RedirectResponse(url="/admin-ui", status_code=303)' in block
    assert 'RedirectResponse(url="/login", status_code=303)' in block


def test_admin_compatibility_entries_do_not_leave_lawyer_on_raw_403():
    _assert_ui_recovery("app/api/maintenance_center.py", "maintenance_ui")
    _assert_ui_recovery("app/api/final_handover_center.py", "final_handover_ui")
    _assert_ui_recovery("app/api/go_live_center.py", "go_live_ui")
    _assert_ui_recovery("app/api/production_center.py", "production_ui")


def test_these_routes_are_mounted_before_role_aware_initial_setup_and_need_own_recovery():
    main = read("app/main.py")
    initial = main.index('(\"initial_setup_wizard\", initial_setup_wizard_router)')

    assert main.index('(\"maintenance_center\", maintenance_center_router)') < initial
    assert main.index('(\"final_handover_center\", final_handover_center_router)') < initial
    assert main.index('(\"go_live_center\", go_live_center_router)') < initial
    assert main.index('(\"production_center\", production_center_router)') < initial
