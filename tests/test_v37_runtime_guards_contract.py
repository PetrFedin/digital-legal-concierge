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
    assert "refund_declined_" in guard
    assert "router.include_router(workdesk_integrity_guard_router)" in setup


def test_client_success_fee_callback_cannot_open_business_stage():
    source = read("app/bot/screens/payment_archive_guard.py")
    wording = read("app/bot/client_wording_patch.py")
    bot = read("app/bot/bot.py")
    assert '@router.callback_query(lambda c: c.data == "pay_success_fee")' in source
    assert "CaseStatus.M1_MONEY_RECEIVED.value" in source
    assert "CaseStatus.M1_WAITING_SUCCESS_FEE.value" in source
    assert "change_status(" not in source
    assert 'CLIENT_ACTIONS["M1_MONEY_RECEIVED"] = ClientAction(' in wording
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


def test_operator_has_one_canonical_authenticated_owner_and_guard_is_compat_only():
    setup = read("app/api/initial_setup_wizard.py")
    guard = read("app/api/operator_guard.py")
    operator = read("app/api/operator.py")
    main = read("app/main.py")

    assert '@router.get("/operator"' in operator
    assert "resolve_document_actor" in operator
    assert '@router.get("/operator"' not in guard
    assert "compatibility" in guard.lower()
    assert "router.include_router(operator_guard_router)" in setup
    assert '@router.get("/admin-ui")' in setup
    assert 'RedirectResponse(url="/admin/workdesk/ui"' in setup
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
    block = source.split("async def route_lawyer_no_show_to_refund", 1)[1]

    assert "CaseStatus.M2_CONSULTATION_DONE" in source
    assert "consultation.status = ConsultationStatus.CANCELLED" in block
    assert "PaymentLifecycleService.transition(" in block
    assert "to_status=PaymentStatus.REFUND_PENDING" in block
    assert "payment.status = PaymentStatus.REFUND_PENDING" not in block
    assert 'slot.status = "available"' in block
    assert "_advance_refund_case" in block
    assert ".with_for_update()" in block


def test_m2_refund_closes_only_after_real_resolution_and_decline_is_retryable():
    guard = read("app/api/refund_resolution_guard.py")
    operator = read("app/api/operator_guard.py")
    assert '@router.post("/admin/refunds/{payment_id}/resolve")' in guard
    assert 'if decision == "refunded"' in guard
    assert "CaseStatus.M2_CONSULTATION_DONE" in guard
    assert "CaseStatus.M2_CLOSED" in guard
    assert '@router.get("/admin/refunds/declined")' in guard
    assert '@router.post("/admin/refunds/{payment_id}/retry")' in guard
    retry_block = guard.split('@router.post("/admin/refunds/{payment_id}/retry")', 1)[1]
    assert "PaymentStatus.REFUND_DECLINED" in retry_block
    assert "PaymentStatus.REFUND_PENDING" in retry_block
    assert "CaseService(db).change_status" not in retry_block
    assert "router.include_router(refund_resolution_guard_router)" in operator


def test_m2_outcomes_have_one_product_owner_and_legacy_resolution_stays_guarded():
    setup = read("app/api/initial_setup_wizard.py")
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
    assert "consultation_outcomes_ui_guard_router" not in setup
    assert '@router.get("/admin/consultation-outcomes/legacy")' in legacy
    assert '@router.post("/admin/consultation-outcomes/{consultation_id}/legacy/resolve")' in legacy
    assert 'VALID_DECISIONS = frozenset({"close", "to_m1", "follow_up"})' in service
    assert 'current_decision != "other"' in service
    assert "CONSULTATION_LEGACY_OUTCOME_RESOLVED" in service


def test_public_health_ready_and_launch_check_do_not_expose_infrastructure():
    setup = read("app/api/initial_setup_wizard.py")
    health_body = setup.split('@router.get("/health")', 1)[1].split('@router.get("/ready")', 1)[0]
    ready_body = setup.split('@router.get("/ready")', 1)[1].split('@router.get("/launch-check")', 1)[0]
    launch_body = setup.split('@router.get("/launch-check")', 1)[1].split('@router.get("/admin-ui")', 1)[0]
    assert "app_env" not in health_body
    assert "database_url" not in health_body
    assert "admin_api_token" not in health_body
    assert 'return {"ok": True, "version": VERSION}' in health_body
    assert "database_url" not in ready_body
    assert "admin_api_token" not in ready_body
    assert "payment_webhook_secret" not in ready_body
    assert "JSONResponse(status_code=503" in ready_body
    assert 'payload = {"ok": ready, "version": VERSION}' in ready_body
    assert "storage_dir" not in launch_body
    assert "payment_provider" not in launch_body
    assert "bot_token" not in launch_body
    assert "await _require_admin" in launch_body


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
    assert 'actor_id=actor_id' in service


def test_staff_search_is_authenticated_bounded_and_html_escaped():
    source = read("app/api/search_center.py")
    assert "resolve_document_actor" in source
    assert "MAX_QUERY_LENGTH = 120" in source
    assert '.replace("%", "\\\\%")' in source
    assert ".limit(20)" in source
    assert "escape(" in source
    assert '@router.get("/search-center/status")' in source
    assert '@router.get("/search-center/ui"' in source


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
    assert 'str(settings.app_env or "").strip().lower() not in {"local", "test"}' in api
    assert "Тестирование записи" not in api
    assert ".limit(1)" in api
    assert ").scalars().first()" in api
    assert "consultations.slot_hold_minutes" in service
    hold_block = service.split("async def hold_slot", 1)[1].split("async def book_available_slot", 1)[0]
    assert "await self.get_hold_minutes()" in hold_block
    assert "self.HOLD_MINUTES" not in hold_block


def test_expired_m2_hold_expires_exact_old_payment_and_writes_history():
    service = read("app/domain/consultations/slot_service.py")
    release = service.split("async def release_expired_holds", 1)[1].split("async def get_available_slots", 1)[0]
    assert 'return f"consultation:{int(consultation_id)}:slot:{int(slot_id)}"' in service
    assert "PaymentCode.M2_CONSULTATION_PAYMENT" in release
    assert "PaymentStatus.PENDING" in release
    assert "PaymentStatus.WAITING_CONFIRMATION" in release
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


def test_staff_financial_sla_and_lawyer_shells_have_server_side_guards():
    staff = read("app/api/staff_ui_guards.py")
    assignment = read("app/api/case_assignment.py")
    lawyer = read("app/api/contract_workspace_ui.py")
    assert '@router.get("/admin/payment-reviews/ui"' in staff
    assert '@router.get("/admin/sla/ui"' in staff
    assert "resolve_document_actor" in staff
    assert "router.include_router(staff_ui_guards_router)" in assignment
    assert '@router.get("/lawyer/ui")' in lawyer
    assert 'RedirectResponse(url="/lawyer/workspace/ui"' in lawyer
    assert '@router.get("/lawyer/consultation-desk/ui"' in lawyer
    assert "guarded_consultation_desk_html" in lawyer


def test_runtime_diagnostics_require_personal_admin_and_public_release_is_minimal():
    runtime = read("app/api/runtime.py")
    assert "resolve_document_actor" in runtime
    assert '@router.get("/snapshot")' in runtime
    assert '@router.get("/case/{case_id}/full")' in runtime
    assert "payment_url" not in runtime
    release_block = runtime.split('@router.get("/release")', 1)[1].split('@router.get("/snapshot")', 1)[0]
    assert '"application_version"' in release_block
    assert '"release"' in release_block
    assert '"git_commit"' in release_block
    assert "migration_heads" not in release_block
    assert "image_repository" not in release_block
    assert "image_tag" not in release_block


def test_fake_manual_payment_is_local_test_only_and_never_enabled_by_demo_mode():
    source = read("app/api/admin.py")
    block = source.split("def manual_payment_confirmation_enabled", 1)[1].split(
        "def payment_can_be_manually_confirmed", 1
    )[0]
    assert 'settings.payment_provider' in block
    assert 'settings.app_env' in block
    assert '{"local", "test"}' in block
    assert "demo_mode" not in block


def test_workdesk_and_operator_navigation_have_no_self_loop_to_legacy_admin_ui():
    workdesk = read("app/api/assignment_queue.py")
    operator = read("app/api/operator_guard.py")
    assert "legacyAdminLink.href='/consultation-slots/ui'" in workdesk
    assert "legacyAdminLink.textContent='Расписание'" in workdesk
    assert "Рабочие разделы администратора" in operator
    assert "/search-center/ui" in operator
    assert "/consultation-slots/ui" in operator


def test_telegram_home_de_duplicates_primary_and_avoids_misleading_parallel_consultation():
    source = read("app/bot/keyboards.py")
    assert "primary_callback = primary_action[1] if primary_action else None" in source
    assert "if callback_data != primary_callback" in source
    active_block = source.split("if primary_action:", 2)[-1]
    assert 'secondary("📁 Моё дело", "my_case_open")' in active_block
    assert 'secondary("📄 Документы", "documents_open")' in active_block
    assert 'secondary("💬 Переписка", "message_history")' in active_block
    assert 'secondary("✉️ Новый вопрос", "message_create")' in active_block
    assert 'secondary("⚖️ Юрист / консультация", "contact_lawyer")' not in active_block
