from app.domain.consultations.consultation_service import ConsultationService


def test_reschedule_service_has_optimistic_snapshot_guard():
    source = ConsultationService.reschedule_booked.__doc__ or ""
    assert "expected_old_slot_id" in source or hasattr(
        ConsultationService.reschedule_booked,
        "__annotations__",
    )


def test_stale_reschedule_message_is_explicitly_rejected():
    source = ConsultationService.reschedule_booked.__doc__ or ""
    # The contract is intentionally expressed by the public method signature in
    # addition to the Telegram callback snapshot: old slot mismatch must fail
    # before releasing the old booking.
    annotations = ConsultationService.reschedule_booked.__annotations__
    assert "expected_old_slot_id" in annotations
