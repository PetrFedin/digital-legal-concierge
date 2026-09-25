from __future__ import annotations

import inspect

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.bot.calculator_draft import CALCULATOR_CASE_ID
from app.bot.screens import calculator
from app.bot.screens.calculator import (
    _future_date_keyboard,
    _present_committed_callback,
    back_planned,
    back_transfer_status,
    calculate_show_callback,
    no,
    planned,
    result_kb,
    to_m1,
    to_m2,
    unknown_calc_data,
    yes,
)
from app.bot.states import CalculatorStates
from app.domain.calculator.rule_revision_service import CalculationRuleRevisionError


class FakeState:
    def __init__(self, data=None, current=None):
        self.data = dict(data or {})
        self.current = current
        self.clear_count = 0

    async def get_data(self):
        return dict(self.data)

    async def set_data(self, data):
        self.data = dict(data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_state(self):
        return self.current

    async def set_state(self, state):
        self.current = getattr(state, "state", state)

    async def clear(self):
        self.data.clear()
        self.current = None
        self.clear_count += 1


class FakeMessage:
    def __init__(self, text="", edit_error: Exception | None = None):
        self.text = text
        self.edit_error = edit_error
        self.edits = []
        self.answers = []

    async def edit_text(self, text, reply_markup=None):
        if self.edit_error is not None:
            raise self.edit_error
        self.edits.append((text, reply_markup))

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))


class FakeCallback:
    def __init__(self, data="", edit_error: Exception | None = None):
        self.data = data
        self.message = FakeMessage(edit_error=edit_error)
        self.callback_answers = []

    async def answer(self, text=None, show_alert=False):
        self.callback_answers.append((text, show_alert))


def _callbacks(markup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]


@pytest.mark.asyncio
async def test_back_navigation_preserves_calculator_values_and_exact_case_binding():
    state = FakeState(
        {
            CALCULATOR_CASE_ID: 77,
            "contract_price": "8500000",
            "planned_transfer_date": "2026-07-01",
            "object_transferred": True,
        },
        current=CalculatorStates.waiting_actual_transfer_date.state,
    )

    transfer = FakeCallback("calc_back_transfer_status:v2:77")
    await back_transfer_status(transfer, state)

    assert state.clear_count == 0
    assert state.data[CALCULATOR_CASE_ID] == 77
    assert state.data["contract_price"] == "8500000"
    assert state.data["planned_transfer_date"] == "2026-07-01"
    assert state.current == CalculatorStates.waiting_object_transfer_status.state
    assert "шаг 3 из 6" in transfer.message.edits[-1][0]

    planned_callback = FakeCallback("calc_back_planned:v2:77")
    await back_planned(planned_callback, state)

    assert state.clear_count == 0
    assert state.data[CALCULATOR_CASE_ID] == 77
    assert state.data["contract_price"] == "8500000"
    assert state.current == CalculatorStates.waiting_planned_transfer_date.state
    assert "01.07.2026" in planned_callback.message.edits[-1][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_handler", (yes, no))
async def test_stale_transfer_buttons_recover_instead_of_using_missing_data(callback_handler):
    state = FakeState(current=CalculatorStates.waiting_object_transfer_status.state)
    callback = FakeCallback(
        "calc_object_transferred_yes:v2:77"
        if callback_handler is yes
        else "calc_object_transferred_no:v2:77"
    )

    if callback_handler is no:
        await callback_handler(callback, state, db=None)
    else:
        await callback_handler(callback, state)

    assert "больше не актуален" in callback.message.edits[-1][0]
    assert _callbacks(callback.message.edits[-1][1]) == [
        "calc_start",
        "my_case_open",
        "nav_home",
    ]


@pytest.mark.asyncio
async def test_future_contractual_date_is_saved_before_information_exit_and_clears_downstream_answers():
    state = FakeState(
        {
            CALCULATOR_CASE_ID: 77,
            "contract_price": "8500000",
            "object_transferred": True,
            "actual_transfer_date": "2026-01-10",
        },
        current=CalculatorStates.waiting_planned_transfer_date.state,
    )
    message = FakeMessage(text="01.01.2099")

    await planned(message, state)

    assert state.data[CALCULATOR_CASE_ID] == 77
    assert state.data["contract_price"] == "8500000"
    assert state.data["planned_transfer_date"] == "2099-01-01"
    assert "object_transferred" not in state.data
    assert "actual_transfer_date" not in state.data
    assert state.current == CalculatorStates.waiting_planned_transfer_date.state
    text, markup = message.answers[-1]
    assert "Срок передачи по ДДУ ещё не наступил" in text
    assert "01.01.2099" in text
    assert _callbacks(markup) == [
        "calc_future_date_consult:v2:77",
        "calc_back_planned:v2:77",
        "nav_home",
    ]


@pytest.mark.asyncio
async def test_early_actual_transfer_moves_to_explicit_participant_fact_instead_of_guessing():
    state = FakeState(
        {
            CALCULATOR_CASE_ID: 77,
            "contract_price": "8500000",
            "planned_transfer_date": "2026-07-10",
            "object_transferred": True,
        },
        current=CalculatorStates.waiting_actual_transfer_date.state,
    )
    message = FakeMessage(text="05.07.2026")

    await calculator.actual(message, state, db=object())

    assert state.data["actual_transfer_date"] == "2026-07-05"
    assert state.current == CalculatorStates.waiting_client_type.state
    text, markup = message.answers[-1]
    assert "шаг 5 из 6" in text
    assert "статус участника ДДУ" in text
    assert _callbacks(markup) == [
        "calc_client_consumer:v2:77",
        "calc_client_other:v2:77",
        "calc_client_unknown:v2:77",
        "nav_home",
    ]


@pytest.mark.asyncio
async def test_committed_calculation_result_falls_back_to_new_message():
    callback = FakeCallback(
        edit_error=TelegramBadRequest(
            method=None,
            message="Bad Request: message can't be edited",
        )
    )

    await _present_committed_callback(
        callback,
        "Расчёт уже сохранён.",
        reply_markup=None,
        saved_notice="Расчёт сохранён.",
    )

    assert callback.message.answers == [("Расчёт уже сохранён.", None)]
    assert callback.callback_answers[-1][0] == "Изменение сохранено. Результат открыт новым сообщением."




@pytest.mark.asyncio
async def test_actual_date_rule_blocker_keeps_saved_input_and_does_not_ask_to_repeat_date(monkeypatch):
    state = FakeState(
        {
            CALCULATOR_CASE_ID: 77,
            "contract_price": "8500000",
            "planned_transfer_date": "2026-05-01",
            "object_transferred": True,
            "actual_transfer_date": "2026-05-25",
        },
        current=CalculatorStates.waiting_actual_transfer_date.state,
    )
    message = FakeMessage(text="25.05.2026")

    class FakeContext:
        async def get_user_from_message(self, _message):
            return object()

    async def fake_bound_case(_ctx, _user, _state):
        return type("Case", (), {"id": 77})()

    async def blocked_result(_state, _db, _case):
        raise CalculationRuleRevisionError(
            "Для даты расчёта нет утверждённой ревизии юридических правил"
        )

    class FakeDB:
        def __init__(self):
            self.commit_called = False
            self.rollback_called = False

        async def commit(self):
            self.commit_called = True

        async def rollback(self):
            self.rollback_called = True

    db = FakeDB()
    monkeypatch.setattr(calculator, "BotContextService", lambda _db: FakeContext())
    monkeypatch.setattr(calculator, "_bound_case", fake_bound_case)
    monkeypatch.setattr(calculator, "calc_result", blocked_result)

    await calculator.calculate_show_message(message, state, db)

    assert db.rollback_called is True
    assert db.commit_called is False
    assert state.data["actual_transfer_date"] == "2026-05-25"
    text, markup = message.answers[-1]
    assert "повторно вводить её не нужно" in text
    assert "опубликованной production-редакции правил" in text
    assert "Повторите дату" not in text
    assert _callbacks(markup) == [
        "calc_back_transfer_status:v2:77",
        "nav_home",
        "my_case_open",
    ]

def test_callback_calculation_commits_before_finishing_case_draft_and_rendering():
    source = inspect.getsource(calculate_show_callback)

    commit = source.index("await db.commit()")
    finish = source.index("await finish_calculator_case(")
    present = source.index("await _present_committed_callback(")
    assert commit < finish < present
    assert "await db.rollback()" in source
    assert "Введённые данные сохранены" in source
    assert "await state.clear()" not in source


def test_all_post_calculation_route_writes_separate_commit_from_presentation():
    for handler in (unknown_calc_data, to_m1, to_m2):
        source = inspect.getsource(handler)
        commit = source.index("await db.commit()")
        present = source.index("await _present_committed_callback(")
        assert commit < present, handler.__name__
        assert "await db.rollback()" in source


def test_positive_calculation_result_exposes_case_bound_m1_m2_postpone_and_recalculation():
    markup = result_kb(77, allow_m1=True)
    texts = [button.text for row in markup.inline_keyboard for button in row]
    callbacks = _callbacks(markup)

    assert texts == [
        "Продолжить ведение дела",
        "🔎 Основания и детализация",
        "💬 Перейти к консультации",
        "Пока изучаю вопрос",
        "🧮 Изменить данные и пересчитать",
        "🏠 Главная",
    ]
    assert callbacks == [
        "calc_continue_m1:v2:77",
        "calc_details:v2:77",
        "calc_to_m2:v2:77",
        "calc_postpone:v2:77",
        "calc_repeat:v2:77",
        "nav_home",
    ]


def test_zero_delay_result_does_not_offer_m1_but_keeps_safe_outcomes():
    markup = result_kb(77, allow_m1=False)
    callbacks = _callbacks(markup)
    texts = [button.text for row in markup.inline_keyboard for button in row]

    assert "Продолжить ведение дела" not in texts
    assert "calc_continue_m1:v2:77" not in callbacks
    assert callbacks == [
        "calc_details:v2:77",
        "calc_to_m2:v2:77",
        "calc_postpone:v2:77",
        "calc_repeat:v2:77",
        "nav_home",
    ]


def test_future_date_consultation_has_its_own_truthful_reason_not_unknown_date_reason():
    source = inspect.getsource(unknown_calc_data)
    assert 'action = "calc_future_date_consult"' in source
    assert "Срок передачи по ДДУ ещё не наступил; клиент запросил консультацию" in source
    assert 'reason = "Клиент не знает дату передачи"' in source


def test_m1_transition_rechecks_latest_calculation_before_status_change():
    source = inspect.getsource(to_m1)
    guard = source.index("require_m1_eligible_calculation")
    status_change = source.index("change_status(")
    commit = source.index("await db.commit()")
    assert guard < status_change < commit
    assert "except CalculatorRouteEligibilityError" in source


def test_future_date_keyboard_is_exact_case_bound():
    assert _callbacks(_future_date_keyboard(77)) == [
        "calc_future_date_consult:v2:77",
        "calc_back_planned:v2:77",
        "nav_home",
    ]