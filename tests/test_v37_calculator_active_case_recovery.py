from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_precalculation_case_states_have_explicit_same_case_resume_cta():
    recovery = read("app/bot/screens/calculator_active_case_recovery.py")

    assert "CaseStatus.NEW.value" in recovery
    assert "CaseStatus.CALCULATOR_STARTED.value" in recovery
    assert '"Продолжить предварительный расчёт"' in recovery
    assert '"Продолжить расчёт"' in recovery
    assert '"calc_recover"' in recovery
    assert "Новый расчёт по другому объекту" in recovery


def test_global_calc_start_is_not_shadowed_by_recovery_router():
    recovery = read("app/bot/screens/calculator_active_case_recovery.py")
    calculator = read("app/bot/screens/calculator.py")

    assert '@router.callback_query(lambda c: callback_matches_action(c.data, "calc_recover"))' in recovery
    assert '@router.callback_query(lambda c: c.data == "calc_start")' not in recovery
    assert "await calculator.calc_start(" not in recovery

    assert '@router.callback_query(lambda c: c.data == "calc_start")' in calculator
    assert "ctx.create_case_from_callback(" in calculator
    assert 'purpose="calculator_start"' in calculator
    assert "A new global Calculate action means a new legal matter" in calculator


def test_recovery_resolves_exact_case_and_never_creates_replacement_case():
    recovery = read("app/bot/screens/calculator_active_case_recovery.py")

    assert "resolve_case_callback_scope(" in recovery
    assert 'action="calc_recover"' in recovery
    assert "str(case.status) not in _RECOVERABLE_CASE_STATUSES" in recovery
    assert 'str(case.route or "") == "M2"' in recovery
    assert "scope.ctx.case_service.select_case_for_user(" in recovery
    assert "case_id=int(case.id)" in recovery
    assert "Данные не изменены и новое дело не создано" in recovery
    assert '("🧮 Новый расчёт", "calc_start")' in recovery


def test_saved_fsm_draft_is_offered_and_missing_draft_restarts_only_same_case_questionnaire():
    recovery = read("app/bot/screens/calculator_active_case_recovery.py")

    assert "has_saved_calculator_draft(data)" in recovery
    assert "draft_case_id == int(case_id)" in recovery
    assert "calculator._draft_summary(data)" in recovery
    assert "draft_step_label(data)" in recovery
    assert '("▶️ Продолжить расчёт", "calc_resume")' in recovery
    assert '("Начать заново", "calc_restart_confirm")' in recovery
    assert "await calculator._start_fresh(" in recovery
    assert "case_id=int(case_id)" in recovery


def test_recovery_router_order_is_safe_because_callback_tokens_are_distinct():
    bot = read("app/bot/bot.py")
    recovery = read("app/bot/screens/calculator_active_case_recovery.py")

    assert "calculator_active_case_recovery.install_active_case_recovery_actions()" in bot
    assert bot.index("calculator_active_case_recovery.router") < bot.index("calculator.router")
    assert 'callback_matches_action(c.data, "calc_recover")' in recovery
    assert 'c.data == "calc_start"' not in recovery
