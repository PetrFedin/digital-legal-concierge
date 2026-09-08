from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_all_client_provenance_layers_are_mounted_before_business_routers():
    bot = read("app/bot/bot.py")

    for middleware in (
        "ConsultationBookingProvenanceMiddleware",
        "ConsultationDescriptionProvenanceMiddleware",
        "ClientMessageProvenanceMiddleware",
        "ClientDocumentUploadStageProtectionMiddleware",
        "DocumentReplacementUploadProtectionMiddleware",
    ):
        assert middleware in bot
        assert bot.index(f"{middleware}())") < bot.index("for router in [")

    assert bot.index("document_upload_binding_guard.router,") < bot.index(
        "document_mutation_guard.router,"
    )
    assert bot.index("telegram_safety_composite.router,") < bot.index(
        "payment_archive_guard.router,"
    )
    assert bot.index("payment_archive_guard.router,") < bot.index("payments.router,")


def test_nested_m1_stale_router_is_not_attached_twice_to_aiogram_dispatcher():
    bot = read("app/bot/bot.py")
    payment_guard = read("app/bot/screens/payment_archive_guard.py")

    assert "router.include_router(m1_stale_view_guard_router)" in payment_guard
    assert "m1_stale_view_guard.router," not in bot


def test_workdesk_enhanced_integrity_endpoint_has_one_live_product_owner():
    product = read("app/api/workdesk_integrity_product.py")
    timeline = read("app/api/workdesk_timeline.py")
    assignment = read("app/api/case_assignment.py")
    main = read("app/main.py")

    assert '"/admin/workdesk/integrity"' in product
    assert "workdesk_integrity_guard" in product
    assert "router.include_router(workdesk_integrity_router)" not in timeline
    assert "workdesk_integrity_guard_router" not in assignment
    assert '("workdesk_integrity_product", workdesk_integrity_product_router)' in main


def test_m2_payments_screen_can_materialize_current_reservation_without_raw_pay_button():
    guard = read("app/bot/screens/payment_archive_guard.py")

    payments_block = guard.split(
        "async def guard_active_m2_payment_list", 1
    )[1].split("async def legacy_consult_pay_is_navigation", 1)[0]
    assert "ClientPaymentReconciliationService" in payments_block
    assert "service.get_or_create_payment(" in payments_block
    assert "PaymentCode.M2_CONSULTATION_PAYMENT" in payments_block
    assert "CaseStatus.M2_PAYMENT_PENDING" in payments_block
    assert "except SlotUnavailableError" in payments_block
    assert "await db.commit()" in payments_block


def test_raw_online_consult_pay_is_navigation_not_provider_mutation():
    guard = read("app/bot/screens/payment_archive_guard.py")

    block = guard.split("async def legacy_consult_pay_is_navigation", 1)[1].split(
        "async def guard_archived_payment_open", 1
    )[0]
    assert "if payments_disabled()" in block
    assert "await payment_screen.consult_pay(callback, db)" in block
    assert "await guard_active_m2_payment_list(callback, db)" in block
    assert "create_payment_link" not in block
    assert "mark_paid" not in block


def test_exact_pay_open_hides_previous_m1_stage_and_creates_link_only_for_current_context():
    guard = read("app/bot/screens/payment_archive_guard.py")

    assert "_M1_CURRENT_STAGE_BY_CODE" in guard
    assert "M1_WAITING_PAYMENT_30000" in guard
    assert "M1_WAITING_PAYMENT_70000" in guard
    assert "M1_WAITING_SUCCESS_FEE" in guard
    block = guard.split("async def guard_archived_payment_open", 1)[1].split(
        "async def guard_archived_fake_success", 1
    )[0]
    assert "_m1_payment_matches_current_stage(payment, case)" in block
    assert "if active_payment and not exact_context" in block
    assert "Сохранённая ссылка скрыта" in block
    assert "create_payment_link(payment)" in block


def test_new_m2_description_entry_binds_after_canonical_intake_handler():
    source = read("app/bot/consultation_description_provenance.py")
    entry = source.split("if isinstance(event, CallbackQuery)", 1)[1].split(
        "if not isinstance(event, Message)", 1
    )[0]

    assert "result = await handler(event, data)" in entry
    assert "_current_context(event, db)" in entry
    assert entry.index("result = await handler(event, data)") < entry.index(
        "_current_context(event, db)"
    )
    assert "consult_description_case_id=int(case.id)" in entry
    assert "consult_description_id=int(consultation.id)" in entry


def test_calendar_date_is_bound_and_failed_slot_retry_keeps_provenance():
    source = read("app/bot/consultation_booking_provenance.py")

    assert '_ADDITIONAL_BOOKING_TOKENS = ("date", "calendar", "book")' in source
    post_slot = source.split("if not _looks_like_slot_choice(value):", 1)[1]
    assert "selection_committed = bool(" in post_slot
    assert "case_status == CaseStatus.M2_PAYMENT_PENDING" in post_slot
    assert "consultation_status == ConsultationStatus.BOOKED" in post_slot
    assert "else:" in post_slot
    assert "consult_booking_message_id=current_message_id" in post_slot
