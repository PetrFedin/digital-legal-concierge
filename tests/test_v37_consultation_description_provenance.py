from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_description_entry_binds_exact_m2_case_and_consultation():
    source = read("app/bot/consultation_description_provenance.py")

    assert '"consult_subject_start"' in source
    assert "consult_description_case_id=int(case.id)" in source
    assert "consult_description_id=int(consultation.id)" in source
    assert "Consultation.case_id == int(case.id)" in source


def test_free_text_is_blocked_if_active_consultation_changed_before_send():
    source = read("app/bot/consultation_description_provenance.py")

    block = source.split("if not isinstance(event, Message)", 1)[1]
    assert 'snapshot.get("consult_description_case_id")' in block
    assert 'snapshot.get("consult_description_id")' in block
    assert "int(case.id) != expected_case_id" in block
    assert "int(consultation.id) != expected_consultation_id" in block
    assert "Текст не был записан в другое дело" in block
    assert "await db.rollback()" in block
    assert "await _recover(" in block


def test_description_recovery_clears_fsm_before_legacy_message_handler_can_run():
    source = read("app/bot/consultation_description_provenance.py")

    recovery = source.split("async def _recover", 1)[1].split(
        "class ConsultationDescriptionProvenanceMiddleware", 1
    )[0]
    assert "await state.clear()" in recovery
    assert '"consult_subject_start"' in recovery
    assert '"my_case_open"' in recovery


def test_description_provenance_is_registered_for_callbacks_and_messages():
    bot = read("app/bot/bot.py")

    assert "ConsultationDescriptionProvenanceMiddleware" in bot
    assert "dispatcher.message.middleware(ConsultationDescriptionProvenanceMiddleware())" in bot
    assert "dispatcher.callback_query.middleware(ConsultationDescriptionProvenanceMiddleware())" in bot
    assert bot.index("ConsultationDescriptionProvenanceMiddleware())") < bot.index(
        "for router in ["
    )
