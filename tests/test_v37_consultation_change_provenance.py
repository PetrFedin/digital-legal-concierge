from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_reschedule_intermediate_calendar_is_bound_to_case_consultation_and_message():
    source = read("app/bot/consultation_change_provenance.py")

    assert '"consult_reschedule_date:"' in source
    assert '"consult_reschedule_slot:"' in source
    assert "consult_reschedule_case_id=int(case.id)" in source
    assert "consult_reschedule_id=int(consultation.id)" in source
    assert "consult_reschedule_message_id=int(event.message.message_id)" in source
    assert "int(case.id) != expected_case_id" in source
    assert "int(consultation.id) != expected_consultation_id" in source
    assert "current_message_id != expected_message_id" in source
    assert "Старый слот не менялся" in source


def test_reschedule_entry_is_case_scoped_and_final_confirmation_retires_calendar():
    source = read("app/bot/consultation_change_provenance.py")

    assert "callback_matches_action(value, _RESCHEDULE_ENTRY)" in source
    entry = source.split("if callback_matches_action(value, _RESCHEDULE_ENTRY):", 1)[1].split(
        "if value.startswith(_RESCHEDULE_CONFIRM_PREFIX):", 1
    )[0]
    assert "resolve_case_callback_scope(" in entry
    assert "allow_legacy_message_case_context=True" in entry
    assert "if scope is None:" in entry

    confirm = source.split("if value.startswith(_RESCHEDULE_CONFIRM_PREFIX):", 1)[1].split(
        "if not value.startswith(_RESCHEDULE_PREFIXES):", 1
    )[0]
    assert "result = await handler(event, data)" in confirm
    assert "await _clear(state)" in confirm


def test_reschedule_provenance_is_registered_before_business_routers():
    bot = read("app/bot/bot.py")

    assert "from app.bot.consultation_change_provenance import ConsultationChangeProvenanceMiddleware" in bot
    assert "dispatcher.callback_query.middleware(ConsultationChangeProvenanceMiddleware())" in bot
    assert bot.index("ConsultationChangeProvenanceMiddleware())") < bot.index("for router in [")


def test_fresh_action_center_reschedule_and_cancel_are_case_bound():
    source = read("app/bot/screens/consultation_booking_ui.py")

    assert 'bound_case_callback("consult_reschedule", case_id)' in source
    assert 'bound_case_callback("consult_cancel", case_id)' in source
