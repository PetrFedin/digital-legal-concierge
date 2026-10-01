from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_non_superadmin_login_uses_canonical_operator_landing():
    auth = read("app/api/auth.py")

    assert 'RedirectResponse(url="/operator", status_code=303)' in auth


def test_operator_is_the_single_human_recovery_owner_for_unsupported_staff():
    operator = read("app/api/operator.py")
    route = operator.split('@router.get("/operator"', 1)[1]

    assert 'return "staff_landing"' in operator
    assert "def _staff_access_recovery_html()" in operator
    assert "Нужно настроить рабочий доступ" in operator
    assert "Техническая или историческая роль" in operator
    assert 'action="/logout"' in operator
    assert 'if actor == "staff_landing":' in route
    assert "HTMLResponse(" in route
    assert "status_code=403" in route
    assert '"Cache-Control": "no-store"' in route
    assert 'RedirectResponse(url="/admin-ui"' not in route


def test_admin_ui_remains_admin_or_superadmin_only_and_recovers_once_to_operator():
    guard = read("app/security/session_guard.py")

    assert '"/admin-ui": frozenset({ROLE_ADMIN, ROLE_SUPERADMIN})' in guard
    assert "protected_ui_roles.intersection(" in guard
    assert 'RedirectResponse(url="/operator", status_code=303)' in guard


def test_staff_recovery_page_contains_no_product_workspace_navigation():
    operator = read("app/api/operator.py")
    helper = operator.split("def _staff_access_recovery_html()", 1)[1].split(
        '@router.get("/operator"', 1
    )[0]

    assert "/admin/workdesk/ui" not in helper
    assert "/lawyer/workspace/ui" not in helper
    assert "/document-access/review/ui" not in helper
    assert "/message-center/ui" not in helper
    assert 'action="/logout"' in helper
