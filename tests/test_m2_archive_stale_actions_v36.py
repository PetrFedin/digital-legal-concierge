from pathlib import Path


CONSULTATIONS_SOURCE = Path("app/bot/screens/consultations.py").read_text(encoding="utf-8")
SCOPE_SOURCE = Path("app/domain/cases/client_case_scope.py").read_text(encoding="utf-8")


def test_stale_m2_recovery_uses_strict_completed_m2_scope():
    assert "latest_completed_strict_m2_case_for_user" in CONSULTATIONS_SOURCE
    assert "latest_completed_strict_m2_case_for_user" in SCOPE_SOURCE


def test_stale_m2_recovery_has_no_rebooking_or_message_cta():
    start = CONSULTATIONS_SOURCE.index("def completed_archive_buttons")
    end = CONSULTATIONS_SOURCE.index("async def _current_booked_context", start)
    block = CONSULTATIONS_SOURCE[start:end]
    assert "consultation_result_open" in block
    assert "payments_open" in block
    assert "message_history" in block
    assert "consult_booking_start" not in block
    assert "message_create" not in block


def test_cancel_and_reschedule_missing_context_use_archive_safe_recovery():
    assert "await _show_missing_booked_context(callback, db, action=\"reschedule\")" in CONSULTATIONS_SOURCE
    assert "await _show_missing_booked_context(callback, db, action=\"cancel\")" in CONSULTATIONS_SOURCE


def test_strict_m2_archive_does_not_borrow_closed_m1_context():
    start = SCOPE_SOURCE.index("async def latest_completed_strict_m2_case_for_user")
    end = SCOPE_SOURCE.index("async def latest_completed_m1_case_for_user", start)
    block = SCOPE_SOURCE[start:end]
    assert "Case.status == CaseStatus.M2_CLOSED" in block
    assert "Case.route == RouteCode.M2.value" in block
    assert "Case.status == CaseStatus.M1_CLOSED" not in block
