from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_backup_center_guard_protects_ui_status_and_verify_routes():
    guard = read("app/api/backup_center_guard.py")

    assert '@router.get("/backup-center/ui"' in guard
    assert '@router.get("/backup-center/status")' in guard
    assert '@router.post("/backup-center/verify/{archive_name}")' in guard
    assert "resolve_document_actor" in guard
    assert "actor.role != ROLE_SUPERADMIN" in guard
    assert "await _superadmin(request, db, x_admin_token)" in guard


def test_backup_center_ui_recovers_anonymous_and_wrong_staff_role():
    guard = read("app/api/backup_center_guard.py")

    assert 'RedirectResponse(url="/login", status_code=303)' in guard
    assert 'RedirectResponse(url="/admin-ui", status_code=303)' in guard
    assert "error.status_code in {403, 409}" in guard


def test_early_guard_preserves_hardened_backup_manager_ui_status_and_verify_logic():
    guard = read("app/api/backup_center_guard.py")
    manager = read("app/api/backup_manager.py")

    assert "from app.api.backup_manager import BACKUP_UI, _status_payload, verify_backup_override" in guard
    assert "return HTMLResponse(BACKUP_UI)" in guard
    assert "return await _status_payload()" in guard
    assert "return await verify_backup_override(" in guard

    # The delegated manager owns the cryptographic restore-fence verification,
    # redacted status payload and critical security-event recording.
    assert "_verify_restorable_archive" in manager
    assert "BackupRevokedError" in manager
    assert "BackupRestoreFenceError" in manager
    assert "BackupSecurityError" in manager
    assert "record_security_event_best_effort" in manager
    assert 'result.pop("database_url_configured", None)' in manager
    assert 'result.pop("encryption_key_id", None)' in manager


def test_backup_guard_shadows_backup_manager_and_legacy_center_with_same_hardened_ux():
    operator = read("app/api/operator_guard.py")
    initial_setup = read("app/api/initial_setup_wizard.py")
    main = read("app/main.py")

    assert "from app.api.backup_center_guard import router as backup_center_guard_router" in operator
    assert "router.include_router(backup_center_guard_router)" in operator
    assert "router.include_router(operator_guard_router)" in initial_setup
    assert main.index('(\"initial_setup_wizard\", initial_setup_wizard_router)') < main.index(
        '(\"backup_manager\", backup_manager_router)'
    )
    assert main.index('(\"backup_manager\", backup_manager_router)') < main.index(
        '(\"backup_center\", backup_center_router)'
    )


def test_legacy_broken_backup_helper_is_not_on_the_effective_route_path():
    legacy = read("app/api/backup_center.py")
    guard = read("app/api/backup_center_guard.py")
    main = read("app/main.py")

    # Historical backup_center.py still contains an old helper signature. It was
    # already shadowed by backup_manager; the v37 early guard additionally owns
    # the public route while reusing backup_manager's hardened implementation.
    assert "return require_security_superadmin(_token(request, header_token))" in legacy
    assert "_require_backup_admin" not in guard
    assert main.index('(\"backup_manager\", backup_manager_router)') < main.index(
        '(\"backup_center\", backup_center_router)'
    )
