from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_expiry_candidate_scan_is_revalidated_under_slot_row_lock():
    service = read("app/domain/consultations/slot_service.py")
    block = service.split("async def release_expired_holds", 1)[1].split(
        "async def get_available_slots", 1
    )[0]

    assert "candidates =" in block
    assert "candidate_by_slot" in block
    assert "locked_slots = list(" in block
    assert ".with_for_update()" in block
    assert 'ConsultationSlot.status == "held"' in block
    assert "ConsultationSlot.hold_expires_at < now" in block
    assert "actual_slot_ids = [int(slot.id) for slot in locked_slots]" in block
    assert "return len(actual_slot_ids)" in block


def test_booked_slot_can_never_be_released_from_stale_expiry_snapshot():
    service = read("app/domain/consultations/slot_service.py")
    block = service.split("async def release_expired_holds", 1)[1].split(
        "async def get_available_slots", 1
    )[0]

    # The final UPDATE must repeat the state/expiry predicates instead of using
    # only candidate ids captured before a concurrent webhook/no-payment booking.
    final_update = block.split("# Keep the state predicate", 1)[1]
    assert "ConsultationSlot.id.in_(actual_slot_ids)" in final_update
    assert 'ConsultationSlot.status == "held"' in final_update
    assert "ConsultationSlot.hold_expires_at.is_not(None)" in final_update
    assert "ConsultationSlot.hold_expires_at < now" in final_update
    assert '.values(\n                    status="available"' in final_update


def test_expiry_keeps_provider_payment_lock_order_before_slot_recheck():
    service = read("app/domain/consultations/slot_service.py")
    block = service.split("async def release_expired_holds", 1)[1].split(
        "async def get_available_slots", 1
    )[0]

    payment_lock = block.index("select(Payment)")
    payment_for_update = block.index(".with_for_update()", payment_lock)
    slot_lock = block.index("locked_slots = list(", payment_for_update)
    assert payment_lock < payment_for_update < slot_lock
    assert "PaymentLifecycleService.transition" in block
    assert "to_status=PaymentStatus.EXPIRED" in block
    assert "provider/no-payment booking won the race" in block


def test_atomic_hold_and_confirm_still_require_expected_slot_state():
    service = read("app/domain/consultations/slot_service.py")

    hold = service.split("async def hold_slot", 1)[1].split("async def book_available_slot", 1)[0]
    confirm = service.split("async def confirm_booking", 1)[1].split("async def release_slot", 1)[0]
    assert 'ConsultationSlot.status == "available"' in hold
    assert "if result.rowcount != 1:" in hold
    assert 'ConsultationSlot.status == "held"' in confirm
    assert "ConsultationSlot.consultation_id == consultation_id" in confirm
    assert "ConsultationSlot.hold_expires_at >= datetime.now(timezone.utc)" in confirm
