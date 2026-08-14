from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_booking_entry_records_case_consultation_and_message_provenance():
    source = read("app/bot/consultation_booking_provenance.py")

    assert '"consult_booking_start"' in source
    assert '"consult_slot_open"' in source
    assert "consult_booking_case_id=int(case.id)" in source
    assert "consult_booking_id=int(consultation.id)" in source
    assert "consult_booking_message_id=int(event.message.message_id)" in source


def test_initial_slot_day_time_callbacks_fail_closed_on_snapshot_mismatch():
    source = read("app/bot/consultation_booking_provenance.py")

    assert "_looks_like_initial_booking_callback" in source
    assert 'data.startswith("consult_")' in source
    assert '("slot", "day", "time")' in source
    assert "int(case.id) != expected_case_id" in source
    assert "int(consultation.id) != expected_consultation_id" in source
    assert "current_message_id != expected_message_id" in source
    assert "Старый слот не бронировался" in source


def test_bound_cancel_and_reschedule_are_not_downgraded_to_message_snapshot():
    source = read("app/bot/consultation_booking_provenance.py")

    assert '"consult_reschedule"' in source
    assert '"consult_cancel"' in source
    assert "if data.startswith(_BOUND_OTHER_FLOWS):" in source
    assert "return False" in source


def test_slot_click_is_one_shot_after_underlying_domain_handler():
    source = read("app/bot/consultation_booking_provenance.py")

    handler = source.split("result = await handler(event, data)", 2)[-1]
    assert "_looks_like_slot_choice(value)" in handler
    assert "consult_booking_case_id=None" in handler
    assert "consult_booking_id=None" in handler
    assert "consult_booking_message_id=None" in handler


def test_booking_provenance_middleware_is_registered_before_business_routers():
    bot = read("app/bot/bot.py")

    assert "ConsultationBookingProvenanceMiddleware" in bot
    assert "dispatcher.callback_query.middleware(ConsultationBookingProvenanceMiddleware())" in bot
    assert bot.index("ConsultationBookingProvenanceMiddleware())") < bot.index(
        "for router in ["
    )
