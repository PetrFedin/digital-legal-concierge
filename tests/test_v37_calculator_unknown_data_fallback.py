from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_legacy_unknown_price_and_date_callbacks_are_navigation_only():
    source = read("app/bot/screens/calculator_unknown_data_guard.py")

    assert '"calc_unknown_price": CalculatorStates.waiting_contract_price.state' in source
    assert '"calc_unknown_date": CalculatorStates.waiting_planned_transfer_date.state' in source
    assert "async def legacy_unbound_unknown_data" in source
    assert "CALCULATOR_CASE_ID" in source
    assert "await db.rollback()" in source
    assert "Переход к консультации не выполнен" in source

    # A raw historical callback has no Case provenance and may never derive a
    # business mutation from whichever Case happens to be present in ambient FSM.
    assert "transfer_to_m2" not in source
    assert "ConsultationIntakeService" not in source
    assert "select_case_for_user" not in source
    assert "create_case" not in source
    assert "await db.commit()" not in source


def test_legacy_unknown_data_recovery_offers_exact_case_resume_without_mutation():
    source = read("app/bot/screens/calculator_unknown_data_guard.py")

    buttons = source.split("def _stale_step_buttons", 1)[1].split(
        "@router.callback_query", 1
    )[0]
    assert 'bound_case_callback("calc_recover", case_id)' in buttons
    assert '("🧮 Новый расчёт", "calc_start")' in buttons
    assert '"▶️ Продолжить расчёт этого обращения"' in buttons


def test_current_unknown_data_actions_are_exact_case_bound_in_canonical_calculator():
    calculator = read("app/bot/screens/calculator.py")

    assert 'bound_case_callback("calc_unknown_price", case_id)' in calculator
    assert 'bound_case_callback("calc_unknown_date", case_id)' in calculator
    assert 'startswith("calc_unknown_price:v2:")' in calculator
    assert 'startswith("calc_unknown_date:v2:")' in calculator
    assert "_require_current_case_callback(" in calculator
    assert "await ctx.case_service.transfer_to_m2(" in calculator
    assert "await db.commit()" in calculator
    assert "await finish_calculator_case(state, case_id=case_id)" in calculator


def test_exact_unknown_data_transition_commits_before_draft_retirement_and_presentation():
    calculator = read("app/bot/screens/calculator.py")
    handler = calculator.split("async def unknown_calc_data", 1)[1].split(
        "async def to_m1", 1
    )[0]

    commit = handler.index("await db.commit()")
    finish = handler.index("await finish_calculator_case(state, case_id=case_id)")
    present = handler.index("await _present_committed_callback(")
    assert commit < finish < present
    assert "await db.rollback()" in handler
    assert "await state.clear()" not in handler


def test_guard_shadows_only_raw_legacy_unknown_callbacks_not_exact_v2_or_global_start():
    bot = read("app/bot/bot.py")
    calculator = read("app/bot/screens/calculator.py")
    guard = read("app/bot/screens/calculator_unknown_data_guard.py")

    assert "calculator_unknown_data_guard," in bot
    assert bot.index("calculator_unknown_data_guard.router,") < bot.index("calculator.router,")
    assert '@router.callback_query(lambda c: c.data in _EXPECTED_FSM)' in guard
    assert 'c.data == "calc_start"' not in guard
    assert 'startswith("calc_unknown_price:v2:")' in calculator
    assert 'startswith("calc_unknown_date:v2:")' in calculator
