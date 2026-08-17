from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_admin_ui_or_login_distinguishes_session_expiry_from_role_mismatch():
    source = read("app/api/initial_setup_wizard.py")
    block = source.split("async def _admin_ui_or_login", 1)[1].split(
        "async def _staff_ui_or_login", 1
    )[0]

    assert "error.status_code == 401" in block
    assert "return None" in block
    assert "error.status_code in {403, 409}" in block
    assert "StaffLandingProblem" in block
    assert "except HTTPException as error:" in block


def test_legacy_setup_ui_bookmarks_recover_wrong_staff_role_to_canonical_landing():
    source = read("app/api/initial_setup_wizard.py")

    assert "def _legacy_admin_ui_recovery" in source
    assert 'RedirectResponse(url="/login", status_code=303)' in source
    assert 'RedirectResponse(url="/admin-ui", status_code=303)' in source

    for function_name in (
        "setup_ui",
        "install_wizard_ui_guard",
        "launch_assistant_ui_guard",
    ):
        block = source.split(f"async def {function_name}", 1)[1]
        assert "actor = await _admin_ui_or_login" in block
        assert "recovery = _legacy_admin_ui_recovery(actor)" in block
        assert "if recovery is not None:" in block
        assert "return recovery" in block
