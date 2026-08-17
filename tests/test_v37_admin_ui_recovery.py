from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_settings_get_and_post_recover_unauthorized_or_wrong_staff_role():
    source = read("app/api/settings_ui.py")

    assert "def _auth_recovery" in source
    assert 'RedirectResponse(url="/login?next=/settings-ui", status_code=303)' in source
    assert 'RedirectResponse(url="/admin-ui", status_code=303)' in source
    assert source.count("except (DocumentAccessError, HTTPException) as error:") >= 2
    assert "error.status_code in {403, 409}" in source


def test_diagnostic_ui_recovers_role_mismatch_and_session_expiry():
    source = read("app/api/diagnostic_center.py")

    assert "except (DocumentAccessError, HTTPException) as exc:" in source
    assert 'RedirectResponse(url="/login", status_code=303)' in source
    assert 'RedirectResponse(url="/admin-ui", status_code=303)' in source
    assert "if(r.status===401){location.href='/login';return}" in source
    assert "if(r.status===403){location.href='/admin-ui';return}" in source
