from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_workdesk_integrity_has_one_product_owner_without_template_mutation():
    timeline = read("app/api/workdesk_timeline.py")
    scanner = read("app/api/workdesk_integrity.py")
    guard = read("app/api/workdesk_integrity_guard.py")
    product = read("app/api/workdesk_integrity_product.py")
    main = read("app/main.py")

    assert "inject_workdesk_integrity" not in timeline
    assert "WORKDESK_HTML =" not in timeline
    assert "m1_contract_confirmation_missing" in scanner
    assert "m2_expired_hold_not_reconciled" in scanner
    assert "m1_money_received_stage_stuck" in guard
    assert "refund_declined_" in guard
    assert '"/admin/workdesk/integrity"' in product
    assert "workdesk_integrity_guard" in product
    assert "workdesk_integrity_product_router" in main


def test_client_success_fee_callback_cannot_open_business_stage():
    source = read("app/bot/screens/payment_archive_guard.py")
    wording = read("app/bot/client_wording_patch.py")
    bot = read("app/bot/bot.py")

    assert '@router.callback_query(lambda c: c.data == "pay_success_fee")' in source
    assert "CaseStatus.M1_MONEY_RECEIVED.value" in source
    assert "CaseStatus.M1_WAITING_SUCCESS_FEE.value" in source
    assert "change_status(" not in source
    money_received_block = wording.split('CLIENT_ACTIONS["M1_MONEY_RECEIVED"]', 1)[1].split(
        'CLIENT_ACTIONS["M1_WAITING_SUCCESS_FEE"]', 1
    )[0]
    assert '"my_case_open"' in money_received_block
    assert '"pay_success_fee"' not in money_received_block
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


def test_operator_and_setup_are_explicit_runtime_owners():
    setup = read("app/api/initial_setup_wizard.py")
    setup_impl = read("app/api/initial_setup_wizard_impl.py")
    guard = read("app/api/operator_guard.py")
    operator = read("app/api/operator.py")

    assert '@router.get("/operator"' in operator
    assert "resolve_document_actor" in operator
    assert "@router." not in guard
    assert "router.add_api_route" not in guard
    assert "operator_guard_router" not in setup
    assert '"/launch-check"' in setup
    assert "await _require_admin" in setup_impl


def test_recovery_changes_only_explicit_locked_rows():
    source = read("app/api/recovery_center.py")

    assert "MAX_RECOVERY_BATCH = 100" in source
    assert 'Payment.id.in_(payment_ids)' in source
    assert 'Notification.id.in_(notification_ids)' in source
    assert "Payment.expires_at <= now" in source
    assert ".with_for_update()" in source


def test_lawyer_no_show_refund_uses_financial_lifecycle_and_releases_slot():
    source = read("app/domain/consultations/no_show_resolution_service.py")
    block = source.split("async def route_lawyer_no_show_to_refund", 1)[1]

    assert "CaseStatus.M2_CONSULTATION_DONE" in source
    assert "consultation.status = ConsultationStatus.CANCELLED" in block
    assert "PaymentLifecycleService.transition(" in block
    assert "to_status=PaymentStatus.REFUND_PENDING" in block
    assert "payment.status = PaymentStatus.REFUND_PENDING" not in block
    assert 'slot.status = "available"' in block
    assert "_advance_refund_case" in block
    assert ".with_for_update()" in block


def test_refund_resolution_is_product_owned_and_retry_uses_domain_lifecycle():
    facade = read("app/api/refund_resolution_guard.py")
    product = read("app/api/refund_product.py")
    service = read("app/domain/payments/refund_service.py")
    operator = read("app/api/operator_guard.py")

    assert 'prefix="/admin/refunds"' in product
    assert "resolve_refund_guard" in product
    assert "declined_refunds" in product
    assert "retry_declined_refund" in product
    assert "router = APIRouter" in facade
    assert "@router." not in facade
    assert "router.add_api_route" not in facade
    retry_api = facade.split("async def retry_declined_refund", 1)[1]
    assert "ConsultationRefundService(db).reopen_declined_refund" in retry_api
    assert "PaymentLifecycleService.transition" not in retry_api
    reopen = service.split("async def reopen_declined_refund", 1)[1].split(
        "async def resolve_refund", 1
    )[0]
    assert "PaymentStatus.REFUND_DECLINED" in reopen
    assert "to_status=PaymentStatus.REFUND_PENDING" in reopen
    assert reopen.count(".with_for_update()") >= 2
    assert "CaseService(db).change_status" not in reopen
    assert "refund_resolution_guard_router" not in operator


def test_consultation_outcomes_have_one_product_owner_and_guarded_legacy_resolution():
    product = read("app/api/consultation_outcomes_product.py")
    retired_guard = read("app/api/consultation_outcomes_ui_guard.py")
    legacy = read("app/api/legacy_consultation_outcome_guard.py")
    service = read("app/domain/consultations/legacy_outcome_resolution_service.py")

    assert 'prefix="/admin/consultation-outcomes"' in product
    assert '@router.get("/ui"' in product
    assert "resolve_document_actor" in product
    assert "legacy_outcome_queue" in product
    assert "resolve_legacy_outcome" in product
    assert "@router." not in retired_guard
    assert "router.add_api_route" not in retired_guard
    assert '@router.get("/admin/consultation-outcomes/legacy")' in legacy
    assert '@router.post("/admin/consultation-outcomes/{consultation_id}/legacy/resolve")' in legacy
    assert 'VALID_DECISIONS = frozenset({"close", "to_m1", "follow_up"})' in service
    assert 'current_decision != "other"' in service


def test_public_health_ready_and_authenticated_launch_check_have_separate_owners():
    main = read("app/main.py")
    setup = read("app/api/initial_setup_wizard.py")
    setup_impl = read("app/api/initial_setup_wizard_impl.py")

    assert '@app.get("/health")' in main
    assert '@app.get("/ready")' in main
    assert '"/launch-check"' in setup
    assert "@router.get(\"/health\")" not in setup
    assert "@router.get(\"/ready\")" not in setup
    assert "async def launch_check(" in setup_impl
    launch_block = setup_impl.split("async def launch_check(", 1)[1].split(
        "async def initial_setup_status(", 1
    )[0]
    assert "await _require_admin" in launch_block


def test_live_settings_are_personal_validated_versioned_and_audited():
    ui = read("app/api/settings_ui.py")
    service = read("app/system/settings_service.py")

    assert "resolve_document_actor" in ui
    assert "expected_updated_at" in ui
    assert "escape(" in ui
    assert 'DEFAULT_SETTINGS.get(key)' in service
    assert "_coerce_value" in service
    assert ".with_for_update()" in service
    assert "expected_updated_at" in service
    assert 'action="SYSTEM_SETTING_UPDATED"' in service


def test_staff_search_is_authenticated_bounded_and_html_escaped():
    source = read("app/api/search_center.py")

    assert "resolve_document_actor" in source
    assert "MAX_QUERY_LENGTH = 120" in source
    assert '.replace("%", "\\\\%")' in source
    assert ".limit(20)" in source
    assert "escape(" in source


def test_bulk_exports_require_personal_admin_and_never_accept_query_token():
    source = read("app/api/exports.py")

    assert "resolve_document_actor" in source
    assert "ADMIN_BULK_EXPORT_CREATED" in source
    assert "Cache-Control" in source
    assert "_csv_cell" in source
    assert "token: str | None = Query" not in source
    assert "admin_api_token" not in source


def test_consultation_schedule_is_role_scoped_and_uses_live_hold_setting():
    api = read("app/api/consultation_slots.py")
    service = read("app/domain/consultations/slot_service.py")

    assert "resolve_document_actor" in api
    assert "Юрист может управлять только собственным расписанием" in api
    assert "Чужое расписание недоступно" in api
    assert "Массовая сверка резервов доступна только администратору" in api
    assert "with_for_update()" in api
    hold_block = service.split("async def hold_slot", 1)[1].split("async def book_available_slot", 1)[0]
    assert "await self.get_hold_minutes()" in hold_block
    assert "self.HOLD_MINUTES" not in hold_block


def test_expired_m2_hold_uses_payment_lifecycle_and_audited_case_transition():
    service = read("app/domain/consultations/slot_service.py")
    release = service.split("async def release_expired_holds", 1)[1].split(
        "async def get_available_slots", 1
    )[0]

    assert "PaymentLifecycleService.transition(" in release
    assert "to_status=PaymentStatus.EXPIRED" in release
    assert "payment.status = PaymentStatus.EXPIRED" not in release
    assert 'action="CONSULTATION_PAYMENT_LINK_EXPIRED"' in release
    assert 'action="CONSULTATION_SLOT_HOLD_EXPIRED"' in release
    assert "CaseStatus.M2_SLOT_PENDING" in release
    assert ".with_for_update()" in release


def test_case_assignment_actor_cannot_be_spoofed_from_payload():
    source = read("app/api/case_assignment.py")

    assert "resolve_document_actor" in source
    assert "payload.get(\"actor_id\")" not in source
    assert "actor_id=int(actor.account_id)" in source
    assert "allow_overload and actor.role != ROLE_SUPERADMIN" in source
    assert "expected_lawyer_id" in source


def test_runtime_diagnostics_require_personal_admin_and_public_release_is_minimal():
    runtime = read("app/api/runtime.py")

    assert "resolve_document_actor" in runtime
    assert '@router.get("/snapshot")' in runtime
    assert '@router.get("/case/{case_id}/full")' in runtime
    assert "payment_url" not in runtime
    release_block = runtime.split('@router.get("/release")', 1)[1].split(
        '@router.get("/snapshot")', 1
    )[0]
    assert '"application_version"' in release_block
    assert '"release"' in release_block
    assert '"git_commit"' in release_block
    assert "migration_heads" not in release_block


def test_fake_manual_payment_is_local_test_only_and_never_enabled_by_demo_mode():
    source = read("app/api/admin_impl.py")
    block = source.split("def manual_payment_confirmation_enabled", 1)[1].split(
        "def payment_can_be_manually_confirmed", 1
    )[0]

    assert "settings.payment_provider" in block
    assert "settings.app_env" in block
    assert '{"local", "test"}' in block
    assert "demo_mode" not in block


def test_operator_navigation_prioritizes_daily_work_and_not_legacy_tools():
    operator = read("app/api/operator.py")

    assert "Рабочие разделы показываются в соответствии с вашей ролью." in operator
    assert "/admin/workdesk/ui" in operator
    assert "/lawyer/workspace/ui" in operator
    assert "/message-center/ui" in operator
    assert "/admin/payment-reviews/ui" in operator


def test_telegram_home_de_duplicates_primary_and_avoids_parallel_consultation_cta():
    source = read("app/bot/keyboards.py")

    assert "primary_callback = primary_action[1] if primary_action else None" in source
    assert "if callback_data != primary_callback" in source
    active_block = source.split("if primary_action:", 2)[-1]
    assert 'secondary("📁 Моё дело", "my_case_open")' in active_block
    assert 'secondary("📄 Документы", "documents_open")' in active_block
    assert 'secondary("💬 Переписка", "message_history")' in active_block
    assert 'secondary("✉️ Новый вопрос", "message_create")' in active_block
    assert 'secondary("⚖️ Юрист / консультация", "contact_lawyer")' not in active_block