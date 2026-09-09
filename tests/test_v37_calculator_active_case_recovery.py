from pathlib import Path

import pytest

from app.bot import calculator_draft as calculator_draft_module
from app.bot.screens import calculator_active_case_recovery as recovery_module

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
    assert "Start a genuinely new calculation Case for this source callback" in calculator
    assert "Different callback id = explicit new operation/new Case" in calculator


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


def test_saved_fsm_draft_is_offered_and_actions_are_case_bound():
    recovery = read("app/bot/screens/calculator_active_case_recovery.py")

    assert "has_saved_calculator_draft(data)" in recovery
    assert "draft_case_id == int(case_id)" in recovery
    assert "calculator._draft_summary(data)" in recovery
    assert "draft_step_label(data)" in recovery
    assert 'bound_case_callback("calc_resume", int(case_id))' in recovery
    assert 'bound_case_callback("calc_restart_confirm", int(case_id))' in recovery
    assert '("▶️ Продолжить расчёт", "calc_resume")' not in recovery
    assert '("Начать заново", "calc_restart_confirm")' not in recovery
    assert "await calculator._start_fresh(" in recovery
    assert "case_id=int(case_id)" in recovery


@pytest.mark.asyncio
async def test_recovered_draft_buttons_carry_exact_case_provenance(monkeypatch):
    async def fake_activate(state, *, case_id: int):
        return {
            calculator_draft_module.CALCULATOR_CASE_ID: int(case_id),
            calculator_draft_module.DRAFT_MARKER: True,
            "contract_price": "8500000",
        }

    monkeypatch.setattr(
        recovery_module,
        "activate_calculator_case_draft",
        fake_activate,
    )

    class FakeState:
        current = "unexpected"

        async def set_state(self, value):
            self.current = value

    class FakeMessage:
        def __init__(self):
            self.edits = []

        async def edit_text(self, text, reply_markup=None):
            self.edits.append((text, reply_markup))

    class FakeCallback:
        def __init__(self):
            self.message = FakeMessage()

    callback = FakeCallback()
    state = FakeState()
    await recovery_module._show_recoverable_calculation(
        callback,
        state,
        case_id=77,
    )

    assert state.current is None
    markup = callback.message.edits[-1][1]
    callbacks = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]
    assert callbacks[:2] == [
        "calc_resume:v2:77",
        "calc_restart_confirm:v2:77",
    ]


def test_recovery_router_order_is_safe_because_callback_tokens_are_distinct():
    bot = read("app/bot/bot.py")
    recovery = read("app/bot/screens/calculator_active_case_recovery.py")

    assert "calculator_active_case_recovery.install_active_case_recovery_actions()" in bot
    assert bot.index("calculator_active_case_recovery.router") < bot.index("calculator.router")
    assert 'callback_matches_action(c.data, "calc_recover")' in recovery
    assert 'c.data == "calc_start"' not in recovery