from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_description_entry_binds_exact_m2_case_and_consultation():
    source = read("app/bot/consultation_description_provenance.py")

    assert '"consult_subject_start"' in source
    assert "callback_matches_action(value, action)" in source
    assert "resolve_case_callback_scope(" in source
    assert "allow_legacy_message_case_context=True" in source
    assert "consult_description_case_id=int(case.id)" in source
    assert "consult_description_id=int(consultation.id)" in source
    assert "Consultation.case_id == int(case.id)" in source


def test_entire_description_callback_flow_is_blocked_after_case_switch():
    source = read("app/bot/consultation_description_provenance.py")

    assert '"consult_description_confirm"' in source
    assert '"consult_description_edit"' in source
    assert '"consult_description_review"' in source
    assert '"consult_subject_case:"' in source
    assert "_is_description_flow_callback(event.data)" in source
    validator = source.split("async def _validate_snapshot", 1)[1].split(
        "async def _clear_provenance_if_flow_finished", 1
    )[0]
    assert 'snapshot.get("consult_description_case_id")' in validator
    assert 'snapshot.get("consult_description_id")' in validator
    assert "int(case.id) != expected_case_id" in validator
    assert "int(consultation.id) != expected_consultation_id" in validator
    assert "Черновик не записан в выбранное сейчас дело" in validator
    assert "await db.rollback()" in validator


def test_free_text_keeps_provenance_until_description_flow_really_finishes():
    source = read("app/bot/consultation_description_provenance.py")

    message_branch = source.split("if not isinstance(event, Message) or state is None:", 1)[1]
    assert "await _validate_snapshot(event, state, db)" in message_branch
    assert "await _clear_provenance_if_flow_finished(state)" in message_branch

    clearer = source.split("async def _clear_provenance_if_flow_finished", 1)[1].split(
        "class ConsultationDescriptionProvenanceMiddleware", 1
    )[0]
    assert "if current_state is None:" in clearer
    assert "consult_description_case_id=None" in clearer
    assert "consult_description_id=None" in clearer


def test_description_recovery_clears_fsm_and_routes_to_explicit_case_selection():
    source = read("app/bot/consultation_description_provenance.py")

    recovery = source.split("async def _recover", 1)[1].split(
        "async def _validate_snapshot", 1
    )[0]
    assert "await state.clear()" in recovery
    assert '("📁 Выбрать обращение", "my_cases_open")' in recovery
    assert '("📁 Моё дело", "my_case_open")' in recovery
    assert '"consult_subject_start"' not in recovery


def test_description_provenance_is_registered_for_callbacks_and_messages():
    bot = read("app/bot/bot.py")

    assert "ConsultationDescriptionProvenanceMiddleware" in bot
    assert "dispatcher.message.middleware(ConsultationDescriptionProvenanceMiddleware())" in bot
    assert "dispatcher.callback_query.middleware(ConsultationDescriptionProvenanceMiddleware())" in bot
    assert bot.index("ConsultationDescriptionProvenanceMiddleware())") < bot.index(
        "for router in ["
    )
