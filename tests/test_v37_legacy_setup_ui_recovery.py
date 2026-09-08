from pathlib import Path

from fastapi.routing import iter_route_contexts

from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _only(method: str, path: str):
    rows = [
        route
        for route in iter_route_contexts(create_app().routes)
        if route.path == path and method in (route.methods or set())
    ]
    assert len(rows) == 1, (method, path, [row.name for row in rows])
    return rows[0]


def test_initial_setup_ui_recovers_anonymous_and_role_mismatch_on_its_own_handler():
    facade = read("app/api/initial_setup_wizard.py")
    impl = read("app/api/initial_setup_wizard_impl.py")

    assert '"/initial-setup-wizard/ui"' in facade
    assert "operator_guard_router" not in facade
    assert "async def _ui_admin_or_redirect(" in impl
    assert 'RedirectResponse(url="/login", status_code=303)' in impl
    assert 'RedirectResponse(url="/operator", status_code=303)' in impl
    assert "error.status_code in {403, 409}" in impl
    assert "gate = await _ui_admin_or_redirect(request, db, x_admin_token)" in impl
    _only("GET", "/initial-setup-wizard/ui")


def test_install_wizard_ui_has_explicit_admin_recovery_without_setup_precedence():
    source = read("app/api/install_wizard.py")

    assert "async def _ui_admin_or_redirect(" in source
    assert 'RedirectResponse(url="/login", status_code=303)' in source
    assert 'RedirectResponse(url="/admin-ui", status_code=303)' in source
    assert "gate = await _ui_admin_or_redirect(request, db, x_admin_token)" in source
    _only("GET", "/install-wizard/ui")


def test_launch_assistant_ui_has_explicit_admin_recovery_without_setup_precedence():
    source = read("app/api/launch_assistant.py")

    assert "async def _ui_admin_or_redirect(" in source
    assert 'RedirectResponse(url="/login", status_code=303)' in source
    assert 'RedirectResponse(url="/admin-ui", status_code=303)' in source
    assert "gate = await _ui_admin_or_redirect(request, db, x_admin_token)" in source
    _only("GET", "/launch-assistant")


def test_setup_related_paths_have_single_runtime_owners():
    for method, path in (
        ("GET", "/launch-check"),
        ("GET", "/initial-setup-wizard/status"),
        ("GET", "/initial-setup-wizard/ui"),
        ("GET", "/install-wizard"),
        ("GET", "/install-wizard/ui"),
        ("GET", "/launch-assistant/status"),
        ("GET", "/launch-assistant"),
    ):
        _only(method, path)
