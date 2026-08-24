from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_live_description_router_accepts_exact_case_entry_and_shows_case_number():
    source = read("app/bot/screens/consultation_description.py")

    assert 'callback_matches_action(c.data, "consult_subject_start")' in source
    assert 'bound_case_callback("consult_subject_start", case_id)' in source
    assert "case_number = str(case.case_number)" in source
    assert "Обращение № {case_number}" in source


def test_description_subject_list_snapshots_orm_values_before_commit():
    source = read("app/bot/screens/consultation_description.py")
    handler = source.split("async def subject_start(callback: CallbackQuery, db, state: FSMContext):", 1)[1].split(
        "async def subject_choice_requires_button", 1
    )[0]

    assert "case_options = [" in handler
    assert handler.index("case_options = [") < handler.index("await db.commit()")
    after_commit = handler.split("await db.commit()", 1)[1]
    assert "item.case_number" not in after_commit
    assert "item.title" not in after_commit


def test_description_confirmation_cannot_save_into_a_different_case_or_consultation():
    source = read("app/bot/screens/consultation_description.py")
    handler = source.split("async def confirm_description(callback: CallbackQuery, state: FSMContext, db):", 1)[1]

    assert "case_id = _description_case_id(data)" in handler
    assert "consultation_id = _description_consultation_id(data)" in handler
    assert "int(saved_case.id) != case_id" in handler
    assert "int(consultation.id) != consultation_id" in handler
    assert handler.index("consultation_status = str(consultation.status)") < handler.index(
        "await db.commit()"
    )
    assert "was_booked or consultation_status == ConsultationStatus.BOOKED.value" in handler


def test_committed_description_skip_documents_action_is_exact_case_bound():
    source = read("app/bot/screens/consultation_description.py")
    presenter = source.split("async def _present_committed_description", 1)[1].split(
        "async def _reset_to_subject_choice", 1
    )[0]

    assert 'f"doc_skip_m2:v2:{case_id}"' in presenter
    assert '"doc_skip_m2"' not in presenter.replace('f"doc_skip_m2:v2:{case_id}"', "")
    assert "Обращение № {case_number}" in presenter


def test_description_draft_reset_preserves_exact_flow_provenance():
    source = read("app/bot/screens/consultation_description.py")
    helper = source.split("async def _reset_to_subject_choice", 1)[1].split(
        '@router.callback_query(lambda c: callback_matches_action(c.data, "consult_subject_start"))', 1
    )[0]

    assert 'payload["consult_description_case_id"] = case_id' in helper
    assert 'payload["consult_description_id"] = consultation_id' in helper
    assert 'payload["consult_description_case_number"] = case_number' in helper
