from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_unknown_price_and_date_require_exact_live_fsm_and_case_binding():
    source = read("app/bot/screens/calculator_unknown_data_guard.py")

    assert '"calc_unknown_price": CalculatorStates.waiting_contract_price.state' in source
    assert '"calc_unknown_date": CalculatorStates.waiting_planned_transfer_date.state' in source
    assert "current_state != expected_state or case_id <= 0" in source
    assert "CALCULATOR_CASE_ID" in source

    stale = source.split("if current_state != expected_state or case_id <= 0:", 1)[1].split(
        "ctx = BotContextService", 1
    )[0]
    assert "await db.rollback()" in stale
    assert "transfer_to_m2" not in stale
    assert "create_case" not in stale
    assert "_stale_step_buttons(case_id)" in stale


def test_stale_unknown_data_recovery_never_reuses_global_new_calculation_token():
    source = read("app/bot/screens/calculator_unknown_data_guard.py")

    buttons = source.split("def _stale_step_buttons", 1)[1].split(
        "@router.callback_query", 1
    )[0]
    assert 'bound_case_callback("calc_recover", case_id)' in buttons
    assert '("🧮 Новый расчёт", "calc_start")' in buttons
    assert '"▶️ Продолжить расчёт этого обращения"' in buttons


def test_unknown_data_transition_uses_exact_existing_case_and_never_bootstraps_another():
    source = read("app/bot/screens/calculator_unknown_data_guard.py")

    live = source.split("ctx = BotContextService", 1)[1]
    assert "ctx.case_service.get_case_for_user(" in live
    assert "user_id=int(user.id)" in live
    assert "case_id=case_id" in live
    assert "current_status not in _ALLOWED_SOURCE_STATUSES" in live
    assert "ctx.case_service.select_case_for_user(" in live
    assert "ctx.case_service.create_case(" not in live
    assert "get_or_create_active_case_for_user" not in live


def test_unknown_data_m2_transition_and_consultation_context_commit_together():
    source = read("app/bot/screens/calculator_unknown_data_guard.py")

    assert "await ctx.case_service.transfer_to_m2(" in source
    assert "ConsultationIntakeService(db).get_or_create_context(" in source
    assert "case_id=int(case.id)" in source
    assert "int(context_case.id) != int(case.id)" in source
    assert source.index("transfer_to_m2(") < source.index("get_or_create_context(")
    assert source.index("get_or_create_context(") < source.index("await db.commit()")
    assert "await db.rollback()" in source
    assert source.index("await db.commit()") < source.index("await state.clear()")


def test_guard_shadows_only_unknown_data_callbacks_not_global_calculator_start():
    bot = read("app/bot/bot.py")
    calculator = read("app/bot/screens/calculator.py")
    guard = read("app/bot/screens/calculator_unknown_data_guard.py")

    assert "calculator_unknown_data_guard," in bot
    assert bot.index("calculator_unknown_data_guard.router,") < bot.index("calculator.router,")
    assert '@router.callback_query(lambda c: c.data in _EXPECTED_FSM)' in guard
    assert 'c.data == "calc_start"' not in guard
    assert 'c.data in {"calc_unknown_price", "calc_unknown_date"}' in calculator
