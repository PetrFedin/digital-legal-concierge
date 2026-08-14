from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_unknown_price_and_date_require_the_exact_live_calculator_fsm_step():
    source = read("app/bot/screens/calculator_unknown_data_guard.py")

    assert '"calc_unknown_price": CalculatorStates.waiting_contract_price.state' in source
    assert '"calc_unknown_date": CalculatorStates.waiting_planned_transfer_date.state' in source
    assert "current_state != expected_state" in source
    stale = source.split("if current_state != expected_state:", 1)[1].split(
        "ctx = BotContextService", 1
    )[0]
    assert "db.rollback" in stale
    assert "case_service" not in stale
    assert "transfer_to_m2" not in stale


def test_unknown_data_fallback_serializes_case_creation_on_the_user_row():
    source = read("app/bot/screens/calculator_unknown_data_guard.py")

    assert "select(User).where(User.id == int(user.id)).with_for_update()" in source
    assert "get_active_case_for_user(user.id)" in source
    assert "create_case(" in source
    assert "CaseStatus.NEW" in source
    assert "_ALLOWED_SOURCE_STATUSES" in source


def test_unknown_data_m2_transition_and_consultation_context_commit_together():
    source = read("app/bot/screens/calculator_unknown_data_guard.py")

    assert "await ctx.case_service.transfer_to_m2(" in source
    assert "ConsultationIntakeService(db).get_or_create_context(user)" in source
    assert "int(context_case.id) != int(case.id)" in source
    assert source.index("transfer_to_m2(") < source.index("get_or_create_context(user)")
    assert source.index("get_or_create_context(user)") < source.index("await db.commit()")
    assert "await db.rollback()" in source
    assert source.index("await db.commit()") < source.index("await state.clear()")


def test_guard_shadows_legacy_raw_unknown_data_handlers():
    bot = read("app/bot/bot.py")
    calculator = read("app/bot/screens/calculator.py")

    assert "calculator_unknown_data_guard," in bot
    assert bot.index("calculator_unknown_data_guard.router,") < bot.index("calculator.router,")
    assert 'c.data in {"calc_unknown_price", "calc_unknown_date"}' in calculator
