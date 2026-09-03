from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_message_provenance_preserves_just_typed_text_instead_of_clearing_it():
    source = read("app/bot/client_message_provenance.py")

    assert "async def _preserve_text_after_target_change" in source
    block = source.split("async def _preserve_text_after_target_change", 1)[1].split(
        "class ClientMessageProvenanceMiddleware", 1
    )[0]
    assert "draft_text=text" in block
    assert "source_message_id=int(event.message_id)" in block
    assert "MessageStates.confirming_message" in block
    assert "message_retarget_current:v2:" in block
    assert "НЕ отправлен" in block
    assert "message_submit" not in block


def test_message_retarget_to_current_case_is_exact_and_still_does_not_send():
    source = read("app/bot/screens/client_message_recovery.py")

    assert '_PREFIX = "message_retarget_current:v2:"' in source
    assert ".with_for_update()" in source
    assert "Case.client_id == int(user.id)" in source
    assert "int(active.id) != int(expected_case_id)" in source
    assert "client_message_case_id=int(case.id)" in source
    assert "MessageStates.confirming_message" in source
    assert "_draft_review_text(data)" in source
    assert "_review_markup()" in source
    assert "MessageService" not in source
    assert "get_or_create_client_message" not in source


def test_preserved_message_recovery_is_mounted_before_legacy_message_router():
    composite = read("app/bot/screens/telegram_safety_composite.py")
    bot = read("app/bot/bot.py")

    assert "client_message_recovery_router" in composite
    assert "router.include_router(client_message_recovery_router)" in composite
    assert bot.index("telegram_safety_composite.router,") < bot.index("messages.router,")


def test_admin_m2_reservation_repair_only_runs_exact_reconciler():
    source = read("app/api/m2_payment_reservation_repair.py")

    block = source.split("async def reconcile_m2_payment_reservation(", 1)[1].split(
        '@router.get(\n    "/admin/workdesk/payments/{payment_id}/reconcile-m2-reservation/ui"', 1
    )[0]
    assert "ClientPaymentReconciliationService(db).reconcile(" in block
    assert "payment_id=int(payment_id)" in block
    assert "case_id=int(case_id)" in block
    assert "if not changed:" in block
    assert "await db.rollback()" in block
    assert "ADMIN_M2_PAYMENT_RESERVATION_RECONCILED" in block
    assert "mark_paid" not in block
    assert "REFUNDED" not in block
    assert "BOOKED" not in block
    assert "mark_booked_after_payment" not in block


def test_admin_m2_reservation_repair_is_mounted_in_live_staff_composite():
    source = read("app/api/case_assignment.py")

    assert "m2_payment_reservation_repair_router" in source
    assert "router.include_router(m2_payment_reservation_repair_router)" in source
    assert source.index("router.include_router(workdesk_integrity_guard_router)") < source.index(
        "router.include_router(m2_payment_reservation_repair_router)"
    )


def test_pending_m2_payment_review_link_redirects_to_nonfinancial_repair_screen():
    source = read("app/api/staff_ui_guards.py")

    block = source.split("async def protected_payment_review_ui(", 1)[1].split(
        '@router.get("/admin/sla/ui"', 1
    )[0]
    assert "PaymentCode.M2_CONSULTATION_PAYMENT.value" in block
    assert "_M2_OPEN_LINK_STATUSES" in block
    assert "/reconcile-m2-reservation/ui" in block
    assert "status_code=303" in block
    assert "return HTMLResponse(PAYMENT_REVIEW_CENTER_HTML)" in block


def test_paid_review_center_remains_the_financial_resolution_surface():
    review = read("app/api/payment_review_center.py")
    guard = read("app/api/staff_ui_guards.py")

    assert "PaymentStatus.PAID_REVIEW" in review
    assert '@router.post("/{payment_id}/resolve")' in review
    assert "PaymentReviewService(db).resolve(" in review
    assert "PAYMENT_REVIEW_CENTER_HTML" in guard
