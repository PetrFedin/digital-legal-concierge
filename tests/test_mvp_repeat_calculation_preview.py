from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.bot.screens import calculator
from app.domain.calculator.preview_receipt import preview_receipt, preview_receipt_fingerprint
from app.bot.states import CalculatorStates


class FakeState:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.current_state = None
        self.clears = 0

    async def get_data(self):
        return dict(self.values)

    async def update_data(self, **data):
        self.values.update(data)

    async def set_state(self, state):
        self.current_state = state

    async def clear(self):
        self.values.clear()
        self.clears += 1
        self.current_state = None


class FakeMessage:
    def __init__(self):
        self.edits = []
        self.answers = []

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))


class FakeCallback:
    def __init__(self):
        self.message = FakeMessage()
        self.data = "calc_preview_start"

    async def answer(self, *args, **kwargs):
        return None


class FakeCaseService:
    def __init__(self, case):
        self.case = case

    async def get_active_case_for_user(self, user_id):
        assert user_id == 44
        return self.case


class FakeContext:
    def __init__(self, case):
        self.case_service = FakeCaseService(case)

    async def get_user_from_callback(self, callback):
        return SimpleNamespace(id=44)


def _callbacks(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


@pytest.mark.asyncio
async def test_active_case_preview_start_is_explicit_and_nonmutating(monkeypatch):
    case = SimpleNamespace(id=31, status="M1_CLAIM_SENT")
    monkeypatch.setattr(calculator, "BotContextService", lambda db: FakeContext(case))
    state = FakeState({"stale_field": "must vanish"})
    callback = FakeCallback()
    await calculator.calc_preview_start(callback, state, object())
    assert state.values["preview_case_id"] == 31
    assert len(state.values["preview_request_key"]) == 32
    assert state.current_state == CalculatorStates.waiting_contract_price
    assert "не изменит действующее дело" in callback.message.edits[-1][0]
    assert case.status == "M1_CLAIM_SENT"


@pytest.mark.asyncio
async def test_preview_stops_if_active_case_changes(monkeypatch):
    state = FakeState({
        "preview_case_id": 31,
        "contract_price": "2000000",
        "planned_transfer_date": "2026-01-01",
        "object_transferred": False,
    })

    with pytest.raises(ValueError, match="изменилось"):
        await calculator.calc_result(state, object(), SimpleNamespace(id=32))

    with pytest.raises(ValueError, match="изменилось"):
        await calculator.calc_result(state, object(), None)


@pytest.mark.asyncio
async def test_preview_uses_shared_formula_and_never_persists(monkeypatch):
    state = FakeState({
        "preview_case_id": 31,
        "contract_price": "2000000",
        "planned_transfer_date": "2026-01-01",
        "object_transferred": True,
        "actual_transfer_date": "2025-12-31",
    })

    class PreviewOnlyService:
        def __init__(self, db):
            pass

        def calculate_preview(self, **kwargs):
            assert kwargs["contract_price"] == Decimal("2000000")
            assert kwargs["actual_transfer_date"] == date(2025, 12, 31)
            return SimpleNamespace(delay_days=0, penalty_amount=Decimal("0"))

        async def calculate_and_save(self, **kwargs):
            raise AssertionError("Preview must never persist to the active case")

    monkeypatch.setattr(calculator, "CalculatorService", PreviewOnlyService)
    result = await calculator.calc_result(state, object(), SimpleNamespace(id=31))
    assert result.delay_days == 0


@pytest.mark.asyncio
async def test_missing_preview_case_has_safe_recovery(monkeypatch):
    monkeypatch.setattr(calculator, "BotContextService", lambda db: FakeContext(None))
    callback = FakeCallback()
    state = FakeState({"previous": "value"})
    await calculator.calc_preview_start(callback, state, object())
    assert state.values == {}
    assert "Активного дела больше нет" in callback.message.edits[-1][0]


@pytest.mark.asyncio
async def test_unknown_preview_field_cannot_transfer_live_case_to_m2(monkeypatch):
    state = FakeState({"preview_case_id": 31})
    callback = FakeCallback()
    callback.data = "calc_unknown_date"
    await calculator.unknown_calc_data(callback, state, object())
    assert state.values == {}
    assert "Текущее дело не изменено" in callback.message.edits[-1][0]
    assert _callbacks(callback.message.edits[-1][1])[0] == "calc_preview_start"


def _example_result():
    return SimpleNamespace(
        contract_price=Decimal("2000000"),
        planned_transfer_date=date(2026, 1, 1),
        calculation_date=date(2026, 3, 1),
        object_transferred=False,
        actual_transfer_date=None,
        delay_days=59,
        key_rate=Decimal("0.16"),
        consumer_multiplier=Decimal("2"),
        penalty_amount=Decimal("125866.67"),
        formula_version="ddu-214fz-v2",
    )


def _preview_callback(token="a" * 32, receipt=None):
    actual = receipt if receipt is not None else preview_receipt(_example_result())
    return _callbacks(calculator.preview_result_kb(token, actual))[0]


def test_preview_result_actions_do_not_mutate_m1_or_m2():
    token = "a" * 32
    receipt = preview_receipt(_example_result())
    callbacks = _callbacks(calculator.preview_result_kb(token, receipt))
    assert "calc_continue_m1" not in callbacks
    assert "calc_to_m2" not in callbacks
    assert callbacks == [
        f"calc_preview_save:{token}:{preview_receipt_fingerprint(receipt)}",
        "calc_preview_start",
        "my_case_open",
        "message_create",
        "nav_home",
    ]
    assert len(callbacks[0].encode("utf-8")) <= 64


@pytest.mark.asyncio
async def test_old_or_forged_save_button_is_rejected_before_any_write():
    state = FakeState({
        "preview_case_id": 31,
        "preview_request_key": "a" * 32,
        "contract_price": "2000000",
        "planned_transfer_date": "2026-01-01",
        "object_transferred": False,
        "preview_receipt": preview_receipt(_example_result()),
    })
    for old_callback in (
        "calc_preview_save",
        _preview_callback(token="b" * 32),
        _preview_callback(token="a" * 32, receipt={
            **preview_receipt(_example_result()), "penalty_amount": "1"
        }),
    ):
        callback = FakeCallback()
        callback.data = old_callback
        await calculator.save_preview_as_new_case(callback, state, object())
        assert "не доступен" in callback.message.edits[-1][0]
    assert state.values["preview_request_key"] == "a" * 32


@pytest.mark.asyncio
async def test_save_uses_shown_date_not_current_day(monkeypatch):
    result = _example_result()
    receipt = preview_receipt(result)
    state = FakeState({
        "preview_case_id": 31,
        "preview_request_key": "a" * 32,
        "contract_price": "2000000",
        "planned_transfer_date": "2026-01-01",
        "object_transferred": False,
        "preview_receipt": receipt,
    })
    callback = FakeCallback()
    callback.data = _preview_callback(receipt=receipt)
    monkeypatch.setattr(calculator, "BotContextService", lambda db: FakeContext(SimpleNamespace(id=31)))
    captured = []

    class StubSave:
        def __init__(self, db):
            pass

        async def save(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(id=81, case_number="DLC-2026-000081")

    class FakeDB:
        async def commit(self):
            pass

        async def rollback(self):
            pass

    monkeypatch.setattr(calculator, "NewObjectSaveService", StubSave)
    await calculator.save_preview_as_new_case(callback, state, FakeDB())
    assert captured[0]["calculation_date"] == date(2026, 3, 1)
    assert captured[0]["expected_receipt"] == receipt
    assert state.clears == 1
    assert "DLC-2026-000081" in callback.message.edits[-1][0]


@pytest.mark.asyncio
async def test_callback_result_persists_receipt_before_save_button(monkeypatch):
    receipt = preview_receipt(_example_result())
    state = FakeState({
        "preview_case_id": 31,
        "preview_request_key": "a" * 32,
        "contract_price": "2000000",
        "planned_transfer_date": "2026-01-01",
        "object_transferred": False,
    })
    callback = FakeCallback()
    monkeypatch.setattr(calculator, "BotContextService", lambda db: FakeContext(SimpleNamespace(id=31)))

    async def fake_calc_result(state, db, case):
        assert case.id == 31
        return _example_result()

    class FakeDB:
        async def commit(self):
            pass

        async def rollback(self):
            pass

    monkeypatch.setattr(calculator, "calc_result", fake_calc_result)
    monkeypatch.setattr(calculator, "format_calculation_result", lambda result: "Предварительный расчёт")
    await calculator.calculate_show_callback(callback, state, FakeDB())
    assert state.values["preview_receipt"] == receipt
    assert state.current_state is None
    assert _callbacks(callback.message.edits[-1][1])[0] == _preview_callback(receipt=receipt)
