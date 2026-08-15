from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_terminal_m2_recovery_preserves_client_meaning_but_not_old_booking():
    source = read("app/domain/consultations/consultation_service.py")

    block = source.split("async def get_or_create_for_case", 1)[1].split(
        "async def _validate_related_case", 1
    )[0]
    assert "_latest_terminal_context_for_case" in block
    assert "client_description=(description or None)" in block
    assert "subject_type=(" in block
    assert "related_case_id=(" in block
    assert "ConsultationStatus.DOCUMENTS_OPTIONAL" in block
    assert "CONSULTATION_CONTEXT_RESTORED" in block
    history = block.split('action="CONSULTATION_CONTEXT_RESTORED"', 1)[1]
    assert '"slot_id": None' in history
    assert '"lawyer_id": None' in history
    assert '"scheduled_at": None' in history


def test_client_payment_reconciler_binds_payment_case_consultation_and_slot():
    source = read("app/domain/payments/client_payment_reconciliation.py")

    assert "select(Payment)" in source and ".with_for_update()" in source
    assert "select(Case)" in source
    assert "payment.case_id" in source
    assert "linked_consultation_id" in source
    assert "linked_slot_id" in source
    assert "int(current.id) != int(linked_consultation_id)" in source
    assert "int(current.slot_id or 0) != int(linked_slot_id)" in source
    assert 'slot.status != "held"' in source
    assert "slot.hold_expires_at" in source
    assert "PaymentService.consultation_reservation_key" in source
    assert 'reason = "reservation_key_mismatch"' in source


def test_stale_current_hold_returns_m2_to_slot_selection_without_resurrecting_booking():
    source = read("app/domain/payments/client_payment_reconciliation.py")

    expire = source.split("async def _expire", 1)[1].split("async def reconcile", 1)[0]
    assert "payment.status = PaymentStatus.EXPIRED" in expire
    assert 'slot.status = "available"' in expire
    assert "consultation.slot_id = None" in expire
    assert "consultation.lawyer_id = None" in expire
    assert "consultation.scheduled_at = None" in expire
    assert "consultation.status = ConsultationStatus.SLOT_PENDING" in expire
    assert "next_status=CaseStatus.M2_SLOT_PENDING" in expire
    assert "CONSULTATION_PAYMENT_LINK_EXPIRED" in expire


def test_previous_reservation_payment_can_expire_without_destroying_new_current_hold():
    source = read("app/domain/payments/client_payment_reconciliation.py")

    reconcile = source.split("async def reconcile", 1)[1]
    previous_consultation = reconcile.split(
        'reason = "payment_points_to_previous_consultation"', 1
    )[0]
    previous_slot = reconcile.split(
        'reason = "payment_points_to_previous_slot"', 1
    )[0]
    assert "restore_slot_selection = False" in reconcile
    assert "restore_slot_selection = True" in reconcile
    assert 'reason = "payment_points_to_previous_consultation"' in reconcile
    assert 'reason = "payment_points_to_previous_slot"' in reconcile
    # The previous-context branches assign a reason only; they do not mark the
    # current hold for release. Current-hold cleanup is reserved for broken/live
    # current slot branches below.
    assert "restore_slot_selection = True" not in previous_consultation.split(
        "elif int(current.id) != int(linked_consultation_id):", 1
    )[-1]
    assert "restore_slot_selection = True" not in previous_slot.split(
        "elif int(current.slot_id or 0) != int(linked_slot_id):", 1
    )[-1]


def test_async_sessions_keep_entities_usable_after_payment_guard_commit():
    session = read("app/db/session.py")
    assert "expire_on_commit=False" in session
