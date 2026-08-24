from pathlib import Path


def test_reschedule_confirmation_binds_exact_consultation_and_old_slot():
    source = Path("app/bot/screens/consultations.py").read_text(encoding="utf-8")
    assert "consultation_id = int(consultation.id)" in source
    assert "selected_slot_id = int(new_slot.id)" in source
    assert "consult_reschedule_confirm:{consultation_id}:{old_slot_id}:{selected_slot_id}" in source
    assert "int(consultation.id) != expected_consultation_id" in source
    assert "expected_old_slot_id=expected_old_slot_id" in source
    assert "Эта кнопка относится к предыдущей записи" in source


def test_cancel_confirmation_binds_exact_consultation_and_slot():
    source = Path("app/bot/screens/consultations.py").read_text(encoding="utf-8")
    assert "consultation_id = int(consultation.id)" in source
    assert "slot_id = int(consultation.slot_id or 0)" in source
    assert "consult_cancel_confirm:{consultation_id}:{slot_id}" in source
    assert "int(consultation.id) != expected_consultation_id" in source
    assert "int(consultation.slot_id or 0) != expected_slot_id" in source


def test_fresh_reschedule_cancel_and_rebooking_recovery_are_case_bound():
    source = Path("app/bot/screens/consultations.py").read_text(encoding="utf-8")

    assert 'retry_action = bound_case_callback("consult_reschedule", case_id)' in source
    assert 'bound_case_callback("consult_cancel", case_id)' in source
    assert 'bound_case_callback("consult_booking_start", case_id)' in source
    assert "Обращение № {case_number}" in source


def test_legacy_generic_cancel_confirmation_is_fail_closed():
    source = Path("app/bot/screens/consultations.py").read_text(encoding="utf-8")
    marker = 'if callback.data == "consult_cancel_confirm":'
    start = source.index(marker)
    end = source.index("try:", start)
    branch = source[start:end]
    assert "Ничего не отменено" in branch
    assert "consultation_booked_open" in branch
    assert "return" in branch
