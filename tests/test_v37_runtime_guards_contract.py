from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_guided_workdesk_mounts_process_integrity():
    source = read("app/api/workdesk_timeline.py")
    scanner = read("app/api/workdesk_integrity.py")
    guard = read("app/api/workdesk_integrity_guard.py")
    setup = read("app/api/initial_setup_wizard.py")
    assert "inject_workdesk_integrity" in source
    assert "router.include_router(workdesk_integrity_router)" in source
    assert '@router.get("/admin/workdesk/integrity")' in scanner
    assert '@router.get("/admin/workdesk/integrity")' in guard
    assert "m1_contract_confirmation_missing" in scanner
    assert "m2_expired_hold_not_reconciled" in scanner
    assert "m1_money_received_stage_stuck" in guard
    assert "router.include_router(workdesk_integrity_guard_router)" in setup


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


def test_lawyer_no_show_refund_leaves_no_booked_case_or_blocked_slot():
    source = read("app/domain/consultations/no_show_resolution_service.py")
    assert "CaseStatus.M2_CONSULTATION_DONE" in source
    assert "consultation.status = ConsultationStatus.CANCELLED" in source
    assert "payment.status = PaymentStatus.REFUND_PENDING" in source
    assert 'slot.status = "available"' in source
    assert "_advance_refund_case" in source
    assert ".with_for_update()" in source


def test_confirmed_m2_refund_closes_consultation_case_only_after_real_resolution():
    guard = read("app/api/refund_resolution_guard.py")
    operator = read("app/api/operator_guard.py")
    assert '@router.post("/admin/refunds/{payment_id}/resolve")' in guard
    assert 'if decision == "refunded"' in guard
    assert "CaseStatus.M2_CONSULTATION_DONE" in guard
    assert "CaseStatus.M2_CLOSED" in guard
    assert "REFUND_DECLINED" not in guard
    assert "router.include_router(refund_resolution_guard_router)" in operator


def test_m2_outcomes_and_legacy_readiness_shells_are_server_guarded():
    setup = read("app/api/initial_setup_wizard.py")
    outcomes_guard = read("app/api/consultation_outcomes_ui_guard.py")
    assert "resolve_document_actor" in outcomes_guard
    assert '@router.get("/admin/consultation-outcomes/ui"' in outcomes_guard
    assert "router.include_router(consultation_outcomes_ui_guard_router)" in setup
    assert '@router.get("/install-wizard")' in setup
    assert '@router.get("/install-wizard/ui")' in setup
    assert '@router.get("/launch-assistant/status")' in setup
    assert '@router.get("/launch-assistant")' in setup


def test_public_health_and_ready_do_not_expose_environment_or_secret_flags():
    setup = read("app/api/initial_setup_wizard.py")
    health_body = setup.split('@router.get("/health")', 1)[1].split('@router.get("/ready")', 1)[0]
    ready_body = setup.split('@router.get("/ready")', 1)[1].split('@router.get("/initial-setup-wizard/status")', 1)[0]
    assert "app_env" not in health_body
    assert "database_url" not in health_body
    assert "admin_api_token" not in health_body
    assert 'return {"ok": True, "version": VERSION}' in health_body
    assert "database_url" not in ready_body
    assert "admin_api_token" not in ready_body
    assert "payment_webhook_secret" not in ready_body
    assert 'return {"ok": ready, "version": VERSION}' in ready_body
