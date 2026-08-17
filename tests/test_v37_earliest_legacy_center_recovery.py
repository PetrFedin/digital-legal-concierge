from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_maintenance_router_is_first_and_owns_legacy_center_guards():
    maintenance = read("app/api/maintenance_center.py")
    main = read("app/main.py")

    assert "router.include_router(legacy_center_guards_router)" in maintenance
    assert main.index('(\"maintenance_center\", maintenance_center_router)') < main.index(
        '(\"initial_setup_wizard\", initial_setup_wizard_router)'
    )
    assert main.index('(\"maintenance_center\", maintenance_center_router)') < main.index(
        '(\"retention_center\", retention_center_router)'
    )


def test_earliest_retention_ui_guard_recovers_wrong_staff_role():
    source = read("app/api/legacy_center_guards.py")
    block = source.split('@router.get("/retention/ui"', 1)[1]

    assert "except DocumentAccessError as error:" in block
    assert "except HTTPException as error:" in block
    assert "error.status_code in {403, 409}" in block
    assert "actor.role != ROLE_SUPERADMIN" in block
    assert "return _role_recovery()" in block
    assert 'RedirectResponse(url="/login", status_code=303)' in block


def test_final_qa_compatibility_entry_never_strands_lawyer_on_raw_403():
    source = read("app/api/legacy_center_guards.py")
    block = source.split('@router.get("/final-qa/ui"', 1)[1].split(
        '@router.get("/retention/ui"', 1
    )[0]

    assert "actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}" in block
    assert "return _role_recovery()" in block
    assert 'RedirectResponse(url="/admin/workdesk/ui", status_code=303)' in block
