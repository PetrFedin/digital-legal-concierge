from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_guided_workdesk_mounts_process_integrity():
    source = read("app/api/workdesk_timeline.py")
    scanner = read("app/api/workdesk_integrity.py")
    assert "inject_workdesk_integrity" in source
    assert "router.include_router(workdesk_integrity_router)" in source
    assert '@router.get("/admin/workdesk/integrity")' in scanner
    assert "m1_contract_confirmation_missing" in scanner
    assert "m2_expired_hold_not_reconciled" in scanner


def test_client_success_fee_callback_cannot_open_business_stage():
    source = read("app/bot/screens/payment_archive_guard.py")
    bot = read("app/bot/bot.py")
    assert '@router.callback_query(lambda c: c.data == "pay_success_fee")' in source
    assert "CaseStatus.M1_MONEY_RECEIVED.value" in source
    assert "CaseStatus.M1_WAITING_SUCCESS_FEE.value" in source
    assert "change_status(" not in source
    assert bot.index("payment_archive_guard.router,") < bot.index("m1_stages.router,")


def test_client_wording_uses_real_document_review_boundary_and_explicit_payments():
    source = read("app/bot/client_wording_patch.py")
    bot = read("app/bot/bot.py")
    assert "install_client_wording()" in bot
    assert 'status == "M1_DOCUMENTS_RECEIVED"' in source
    assert "ждём начала проверки" in source
    assert "Оплатить 30 000 ₽" in source
    assert "Оплатить 70 000 ₽" in source
    assert "Оплатить финальный процент" in source


def test_legacy_operator_is_shadowed_by_early_authenticated_guard():
    setup = read("app/api/initial_setup_wizard.py")
    guard = read("app/api/operator_guard.py")
    main = read("app/main.py")
    assert "router.include_router(operator_guard_router)" in setup
    assert "resolve_document_actor" in guard
    assert '@router.get("/operator")' in guard
    assert main.index('(\"initial_setup_wizard\", initial_setup_wizard_router)') < main.index(
        '(\"operator\", operator_router)'
    )


def test_recovery_changes_only_explicit_locked_rows():
    source = read("app/api/recovery_center.py")
    assert "MAX_RECOVERY_BATCH = 100" in source
    assert 'Payment.id.in_(payment_ids)' in source
    assert 'Notification.id.in_(notification_ids)' in source
    assert "Payment.expires_at <= now" in source
    assert ".with_for_update()" in source
