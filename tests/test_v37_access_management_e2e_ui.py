from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_access_ui_exposes_create_update_deactivate_password_and_mfa_reset_paths():
    source = read("app/api/access_management.py")

    html = source.split('ACCESS_HTML = r"""', 1)[1]
    assert "createUser(this)" in html
    assert "saveUser(${u.id},this)" in html
    assert "resetMfa(${u.id},this)" in html
    assert "is_active:isActive" in html
    assert "if(password)payload.password=password" in html
    assert "method:'PATCH'" in html
    assert "/mfa/reset" in html


def test_access_ui_sends_exact_account_snapshot_for_update_and_mfa_reset():
    source = read("app/api/access_management.py")
    html = source.split('ACCESS_HTML = r"""', 1)[1]

    assert "expected_account_version:Number(u.account_version)" in html
    assert "body:JSON.stringify({expected_account_version:Number(u.account_version)})" in html
    assert "Учётная запись уже изменена другим действием" in source
    assert "user.account_version = int(user.account_version or 1) + 1" in source


def test_access_ui_requires_explicit_confirmation_for_session_sensitive_changes():
    source = read("app/api/access_management.py")

    html = source.split('ACCESS_HTML = r"""', 1)[1]
    assert "rolesChanged||activeChanged||password" in html
    assert "Активные сессии будут отозваны" in html
    assert "Сбросить MFA" in html
    assert "при следующем входе потребуется новая настройка" in html


def test_access_ui_keeps_canonical_now_main_step_and_secondary_user_controls():
    source = read("app/api/access_management.py")

    html = source.split('ACCESS_HTML = r"""', 1)[1]
    assert '>Сейчас<' in html
    assert html.count("Главный следующий шаг") >= 4
    assert "Текущие пользователи" in html
    assert "Управлять учётной записью" in html
    assert 'href="/audit-center/ui"' in html
    assert 'href="/operator"' in html


def test_last_superadmin_guard_remains_server_side_not_just_visual():
    source = read("app/api/access_management.py")

    backend = source.split("async def update_user", 1)[1].split(
        "@router.post(\"/users/{user_id}/mfa/reset\")", 1
    )[0]
    assert "removes_superadmin" in backend
    assert "active_superadmin_count(" in backend
    assert "Нельзя отключить или лишить прав последнего суперадминистратора" in backend
