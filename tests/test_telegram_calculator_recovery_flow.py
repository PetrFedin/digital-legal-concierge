from __future__ import annotations

import inspect

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.bot.screens.calculator import (
    _present_committed_callback,
    back_planned,
    back_transfer_status,
    calculate_show_callback,
    no,
    result_kb,
    to_m1,
    to_m2,
    unknown_calc_data,
    yes,
)
from app.bot.states import CalculatorStates


class FakeState:
    def __init__(self, data=None, current=None):
        self.data = dict(data or {})
        self.current = current
        self.clear_count = 0

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, state):
        self.current = getattr(state, "state", state)

    async def clear(self):
        self.data.clear()
        self.current = None
        self.clear_count += 1


class FakeMessage:
    def __init__(self, edit_error: Exception | None = None):
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
async def test_back_navigation_preserves_calculator_values():
    state = FakeState(
        {
            "contract_price": "8500000",
            "planned_transfer_date": "2026-07-01",
            "object_transferred": True,
        },
        current=CalculatorStates.waiting_actual_transfer_date.state,
    )

    transfer = FakeCallback("calc_back_transfer_status")
    await back_transfer_status(transfer, state)

    assert state.clear_count == 0
    assert state.data["contract_price"] == "8500000"
    assert state.data["planned_transfer_date"] == "2026-07-01"
    assert state.current == CalculatorStates.waiting_object_transfer_status.state
    assert "шаг 3 из 4" in transfer.message.edits[-1][0]

    planned = FakeCallback("calc_back_planned")
    await back_planned(planned, state)

    assert state.clear_count == 0
    assert state.data["contract_price"] == "8500000"
    assert state.current == CalculatorStates.waiting_planned_transfer_date.state
    assert "01.07.2026" in planned.message.edits[-1][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_handler", (yes, no))
async def test_stale_transfer_buttons_recover_instead_of_using_missing_data(callback_handler):
    state = FakeState(current=CalculatorStates.waiting_object_transfer_status.state)
    callback = FakeCallback(
        "calc_object_transferred_yes"
        if callback_handler is yes
        else "calc_object_transferred_no"
    )

    if callback_handler is no:
        await callback_handler(callback, state, db=None)
    else:
        await callback_handler(callback, state)

    assert state.clear_count == 1
    assert "больше не актуален" in callback.message.edits[-1][0]
    assert _callbacks(callback.message.edits[-1][1]) == [
        "calc_start",
        "my_case_open",
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


def test_callback_calculation_commits_before_clearing_state_and_rendering():
    source = inspect.getsource(calculate_show_callback)

    commit = source.index("await db.commit()")
    clear = source.index("await state.clear()")
    present = source.index("await _present_committed_callback(")
    assert commit < clear < present
    assert "await db.rollback()" in source
    assert "Введённые данные сохранены" in source


def test_all_post_calculation_route_writes_separate_commit_from_presentation():
    for handler in (unknown_calc_data, to_m1, to_m2):
        source = inspect.getsource(handler)
        commit = source.index("await db.commit()")
        present = source.index("await _present_committed_callback(")
        assert commit < present, handler.__name__
        assert "await db.rollback()" in source


def test_calculator_result_actions_are_clear_and_have_an_exit():
    markup = result_kb()
    texts = [button.text for row in markup.inline_keyboard for button in row]
    callbacks = _callbacks(markup)

    assert texts[:3] == [
        "Продолжить ведение дела",
        "💬 Перейти к консультации",
        "Пока изучаю вопрос",
    ]
    assert callbacks == [
        "calc_continue_m1",
        "calc_to_m2",
        "calc_postpone",
        "nav_home",
    ]
