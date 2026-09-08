from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_m2_calendar_uses_shared_business_timezone_for_day_and_time():
    source = read("app/bot/screens/consultation_intake.py")

    assert "from app.presentation_time import format_business_datetime, to_business_timezone" in source
    assert "return to_business_timezone(value).date().isoformat()" in source
    assert 'pattern="%d.%m.%Y"' in source
    assert 'pattern="%H:%M"' in source
    assert "slot.starts_at.date().isoformat()" not in source


def test_fresh_m2_handoff_buttons_are_exact_case_bound():
    intake = read("app/bot/screens/consultation_intake.py")
    navigation = read("app/bot/screens/consultation_navigation_guard.py")
    documents = read("app/bot/screens/document_mutation_guard.py")
    action_center = read("app/bot/screens/consultation_booking_ui.py")

    assert 'f"doc_skip_m2:v2:{case_id}"' in intake
    assert 'bound_case_callback("consult_pay", case_id)' in intake
    assert intake.count('bound_case_callback("consult_booking_start", case_id)') >= 3
    assert 'bound_case_callback("consult_booking_start", case_id)' in navigation
    assert documents.count('bound_case_callback("consult_booking_start", case_id)') >= 2
    assert 'bound_case_callback(primary[1], case_id)' in action_center
    assert 'bound_case_callback("consult_reschedule", case_id)' in action_center
    assert 'bound_case_callback("consult_cancel", case_id)' in action_center


def test_booking_entry_is_scoped_before_slot_query_but_internal_race_refresh_stays_usable():
    source = read("app/bot/screens/consultation_intake.py")
    handler = source.split("async def booking_start(callback: CallbackQuery, db):", 1)[1].split(
        '@router.callback_query(lambda c: c.data.startswith("consult_date:"))', 1
    )[0]

    assert "action = _booking_entry_action(callback.data)" in handler
    assert "scope = None" in handler
    assert "if action is not None:" in handler
    assert "resolve_case_callback_scope(" in handler
    assert "allow_legacy_message_case_context=True" in handler
    assert handler.index("resolve_case_callback_scope(") < handler.index("_prepare_slots(")
    assert "Internal recovery calls originate only from already validated date/slot" in handler
    assert "if action is None:\n        return" not in handler

    date_handler = source.split("async def choose_date(callback: CallbackQuery, db):", 1)[1].split(
        "async def _show_booked", 1
    )[0]
    slot_handler = source.split("async def choose_slot(callback: CallbackQuery, db):", 1)[1].split(
        '@router.callback_query(lambda c: payments_disabled()', 1
    )[0]
    assert "await booking_start(callback, db)" in date_handler
    assert "await booking_start(callback, db)" in slot_handler


def test_m2_description_and_slot_views_snapshot_orm_values_before_transaction_boundary():
    source = read("app/bot/screens/consultation_intake.py")

    subject = source.split("async def subject_start(callback: CallbackQuery, db, state: FSMContext):", 1)[1].split(
        '@router.callback_query(lambda c: c.data.startswith("consult_subject_case:"))', 1
    )[0]
    assert subject.index("case_id = int(case.id)") < subject.index("await db.commit()")
    assert ".where(Case.id != case_id)" in subject

    prepare = source.split("async def _prepare_slots(callback: CallbackQuery, db, *, scope=None):", 1)[1].split(
        '@router.callback_query(lambda c: _booking_entry_action(c.data) is not None)', 1
    )[0]
    assert prepare.index("slot_views = [_slot_view(slot) for slot in slots]") < prepare.index(
        "await db.commit()"
    )
