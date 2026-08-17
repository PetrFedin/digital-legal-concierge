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


def test_backup_verify_keeps_existing_crypto_and_security_event_boundaries():
    guard = read("app/api/backup_center_guard.py")

    assert "_safe_backup_root()" in guard
    assert "_safe_archive(root, archive_name)" in guard
    assert "_verify_restorable_archive" in guard
    assert "BackupRevokedError" in guard
    assert "BackupRestoreFenceError" in guard
    assert "BackupSecurityError" in guard
    assert "record_security_event_best_effort" in guard
    assert "actor.account_id" in guard


def test_backup_guard_shadows_legacy_routes_before_backup_center_router():
    operator = read("app/api/operator_guard.py")
    initial_setup = read("app/api/initial_setup_wizard.py")
    main = read("app/main.py")

    assert "from app.api.backup_center_guard import router as backup_center_guard_router" in operator
    assert "router.include_router(backup_center_guard_router)" in operator
    assert "router.include_router(operator_guard_router)" in initial_setup
    assert main.index('(\"initial_setup_wizard\", initial_setup_wizard_router)') < main.index(
        '(\"backup_center\", backup_center_router)'
    )


def test_legacy_backup_helper_is_not_used_by_early_guard():
    legacy = read("app/api/backup_center.py")
    guard = read("app/api/backup_center_guard.py")

    # Historical helper calls the now-async security resolver with the old sync
    # contract. The early exact guard must not delegate authorization to it.
    assert "return require_security_superadmin(_token(request, header_token))" in legacy
    assert "_require_backup_admin" not in guard
