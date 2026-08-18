from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_access_role_guard_requires_product_workspace_role():
    guard = read("app/api/access_role_guard.py")

    assert "PRODUCT_WORKSPACE_ROLES" in guard
    assert "ROLE_ADMIN" in guard
    assert "ROLE_SUPERADMIN" in guard
    assert "ROLE_LAWYER" in guard
    assert "PRODUCT_WORKSPACE_ROLES.intersection(roles)" in guard
    assert "Оператор" in guard
    assert "Тестировщик" in guard
    assert "дополнительными техническими ролями" in guard


def test_access_role_guard_rejects_conflicting_admin_and_lawyer_responsibility():
    guard = read("app/api/access_role_guard.py")

    assert "ROLE_LAWYER in roles" in guard
    assert "ROLE_ADMIN in roles or ROLE_SUPERADMIN in roles" in guard
    assert "Нельзя совмещать роли" in guard
    assert "отдельные персональные учётные" in guard
    assert "Административную и юридическую ответственность" in guard


def test_access_role_guard_covers_create_and_role_update():
    guard = read("app/api/access_role_guard.py")

    assert '@router.post("/access/users")' in guard
    assert '_validate_product_workspace_role(payload, required=True)' in guard
    assert '@router.patch("/access/users/{user_id}")' in guard
    assert '_validate_product_workspace_role(payload, required=False)' in guard
    assert "legacy_create_user" in guard
    assert "legacy_update_user" in guard


def test_access_management_ui_is_server_side_superadmin_guarded():
    guard = read("app/api/access_role_guard.py")

    assert '@router.get("/access/ui"' in guard
    assert "resolve_document_actor" in guard
    assert "actor.role != ROLE_SUPERADMIN" in guard
    assert 'url="/login"' in guard
    assert "Базовая рабочая роль обязательна" in guard


def test_access_role_guard_is_mounted_before_legacy_access_management_router():
    initial = read("app/api/initial_setup_wizard.py")
    main = read("app/main.py")

    assert "access_role_guard_router" in initial
    assert "router.include_router(access_role_guard_router)" in initial
    assert main.index('("initial_setup_wizard", initial_setup_wizard_router)') < main.index(
        '("access_management", access_management_router)'
    )
