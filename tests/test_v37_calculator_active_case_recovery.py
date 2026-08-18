from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_precalculation_case_states_have_real_client_cta():
    recovery = read("app/bot/screens/calculator_active_case_recovery.py")

    assert "CaseStatus.NEW.value" in recovery
    assert "CaseStatus.CALCULATOR_STARTED.value" in recovery
    assert '"Начать предварительный расчёт"' in recovery
    assert '"Продолжить расчёт"' in recovery
    assert '"calc_start"' in recovery
    assert "новое дело создаваться не будет" in recovery


def test_calc_start_recovers_same_active_case_instead_of_my_case_loop():
    recovery = read("app/bot/screens/calculator_active_case_recovery.py")

    assert '@router.callback_query(lambda c: c.data == "calc_start")' in recovery
    assert "case = await ctx.case_service.get_active_case_for_user(user.id)" in recovery
    assert "str(case.status) in _RECOVERABLE_CASE_STATUSES" in recovery
    assert 'str(case.route or "") != "M2"' in recovery
    assert "await _show_recoverable_calculation(callback, state)" in recovery
    assert "await calculator.calc_start(callback, state, db)" in recovery


def test_saved_fsm_draft_is_offered_and_missing_draft_restarts_questionnaire_only():
    recovery = read("app/bot/screens/calculator_active_case_recovery.py")

    assert "has_saved_calculator_draft(data)" in recovery
    assert "calculator._draft_summary(data)" in recovery
    assert "draft_step_label(data)" in recovery
    assert '("▶️ Продолжить расчёт", "calc_resume")' in recovery
    assert '("Начать заново", "calc_restart_confirm")' in recovery
    assert "await calculator._start_fresh(callback, state)" in recovery
    assert "Keep the same Case row" in recovery


def test_recovery_router_precedes_legacy_calculator_handler_and_installs_actions():
    bot = read("app/bot/bot.py")

    assert "calculator_active_case_recovery.install_active_case_recovery_actions()" in bot
    assert bot.index("calculator_active_case_recovery.router") < bot.index("calculator_unknown_data_guard.router")
    assert bot.index("calculator_active_case_recovery.router") < bot.index("calculator.router")
