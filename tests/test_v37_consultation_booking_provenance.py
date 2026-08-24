from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_booking_entry_records_case_consultation_and_message_provenance():
    source = read("app/bot/consultation_booking_provenance.py")

    assert '"consult_booking_start"' in source
    assert '"consult_slot_open"' in source
    assert "callback_matches_action" in source
    assert "entry_action = _booking_entry_action(value)" in source
    assert "consult_booking_case_id=int(case.id)" in source
    assert "consult_booking_id=int(consultation.id)" in source
    assert "consult_booking_message_id=int(event.message.message_id)" in source


def test_canonical_booking_entry_checks_case_scope_before_slot_preparation():
    source = read("app/bot/screens/consultation_intake.py")

    assert "@router.callback_query(lambda c: _booking_entry_action(c.data) is not None)" in source
    handler = source.split("async def booking_start(callback: CallbackQuery, db):", 1)[1].split(
        '@router.callback_query(lambda c: c.data.startswith("consult_date:"))', 1
    )[0]
    assert "resolve_case_callback_scope(" in handler
    assert "allow_legacy_message_case_context=True" in handler
    assert "scope.case is None" in handler
    assert handler.index("resolve_case_callback_scope(") < handler.index("_prepare_slots(")


def test_initial_slot_day_time_callbacks_fail_closed_on_snapshot_mismatch():
    source = read("app/bot/consultation_booking_provenance.py")

    assert "_looks_like_initial_booking_callback" in source
    assert 'data.startswith("consult_")' in source
    assert '("slot", "day", "time")' in source
    assert "int(case.id) != expected_case_id" in source
    assert "int(consultation.id) != expected_consultation_id" in source
    assert "current_message_id != expected_message_id" in source
    assert "Старый слот не бронировался" in source
    assert '("📁 Выбрать обращение", "my_cases_open")' in source


def test_stale_calendar_recovery_never_emits_raw_mutating_case_actions_when_case_known():
    source = read("app/bot/consultation_booking_provenance.py")

    recovery = source.split("async def _recover", 1)[1].split(
        "class ConsultationBookingProvenanceMiddleware", 1
    )[0]
    assert 'bound_case_callback("consult_booking_start", int(case_id))' in recovery
    assert 'bound_case_callback("message_create", int(case_id))' in recovery
    assert 'case_id: int | None = None' in recovery

    mismatch = source.split("current_message_id =", 1)[1].split(
        "result = await handler(event, data)", 1
    )[0]
    assert "current_case_id = int(case.id) if case is not None else None" in mismatch
    assert "case_id=current_case_id" in mismatch


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
