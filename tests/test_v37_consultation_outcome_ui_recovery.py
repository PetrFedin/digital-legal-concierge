from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_consultation_outcome_ui_is_early_guarded_and_preserves_guided_patches():
    source = read("app/api/consultation_outcomes_ui_guard.py")
    initial_setup = read("app/api/initial_setup_wizard.py")
    main = read("app/main.py")

    assert '@router.get("/admin/consultation-outcomes/ui"' in source
    assert "_inject_client_no_show_ui(OUTCOMES_HTML)" in source
    assert "inject_legacy_outcome_ui(html)" in source
    assert "router.include_router(legacy_consultation_outcome_router)" in source
    assert "router.include_router(consultation_outcomes_ui_guard_router)" in initial_setup
    assert main.index('(\"initial_setup_wizard\", initial_setup_wizard_router)') < main.index(
        '(\"guided_consultation_outcomes\", guided_consultation_outcomes_router)'
    )


def test_consultation_outcome_ui_recovers_wrong_or_incomplete_staff_role():
    source = read("app/api/consultation_outcomes_ui_guard.py")

    assert "except DocumentAccessError as error:" in source
    assert "except HTTPException as error:" in source
    assert "error.status_code in {403, 409}" in source
    assert 'RedirectResponse(url="/login", status_code=303)' in source
    assert 'RedirectResponse(url="/admin-ui", status_code=303)' in source
    assert "actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}" in source
