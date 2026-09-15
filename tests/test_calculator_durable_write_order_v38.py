from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User as TelegramUser

from app.bot import calculator_durable
from app.bot.states import CalculatorStates


def _message(text: str) -> Message:
    return Message(
        message_id=10,
        date=datetime.now(timezone.utc),
        chat=Chat(id=7001, type="private"),
        from_user=TelegramUser(id=9001, is_bot=False, first_name="Client"),
        text=text,
    )


def _state(*, state_name: str, data: dict):
    return SimpleNamespace(
        get_state=AsyncMock(return_value=state_name),
        get_data=AsyncMock(return_value=dict(data)),
    )


@pytest.mark.asyncio
async def test_accepted_price_commits_before_canonical_handler(monkeypatch):
    order: list[str] = []
    state = _state(
        state_name=CalculatorStates.waiting_contract_price.state,
        data={"calculator_case_id": 42},
    )
    db = SimpleNamespace(
        commit=AsyncMock(side_effect=lambda: order.append("commit")),
        rollback=AsyncMock(),
    )
    service = SimpleNamespace(
        get=AsyncMock(return_value=None),
        save_price=AsyncMock(side_effect=lambda **_: order.append("save")),
    )
    monkeypatch.setattr(
        calculator_durable,
        "CalculationIntakeService",
        lambda _db: service,
    )

    async def owned_case(*_args, **_kwargs):
        return SimpleNamespace(status="CALCULATOR_STARTED", route=None)

    monkeypatch.setattr(calculator_durable, "_owned_calculator_case", owned_case)

    async def handler(_event, _data):
        order.append("handler")
        return "ok"

    result = await calculator_durable.DurableCalculatorIntakeMiddleware()(
        handler,
        _message("8500000"),
        {"state": state, "db": db},
    )

    assert result == "ok"
    assert order == ["save", "commit", "handler"]
    service.save_price.assert_awaited_once()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_database_failure_blocks_handler_and_fsm_progression(monkeypatch):
    called = False
    state = _state(
        state_name=CalculatorStates.waiting_contract_price.state,
        data={"calculator_case_id": 42},
    )
    db = SimpleNamespace(
        commit=AsyncMock(side_effect=RuntimeError("db unavailable")),
        rollback=AsyncMock(),
    )
    service = SimpleNamespace(
        get=AsyncMock(return_value=None),
        save_price=AsyncMock(),
    )
    monkeypatch.setattr(
        calculator_durable,
        "CalculationIntakeService",
        lambda _db: service,
    )

    async def owned_case(*_args, **_kwargs):
        return SimpleNamespace(status="CALCULATOR_STARTED", route=None)

    monkeypatch.setattr(calculator_durable, "_owned_calculator_case", owned_case)
    warning = AsyncMock()
    monkeypatch.setattr(calculator_durable, "_warn_durable_failure", warning)

    async def handler(_event, _data):
        nonlocal called
        called = True

    result = await calculator_durable.DurableCalculatorIntakeMiddleware()(
        handler,
        _message("8500000"),
        {"state": state, "db": db},
    )

    assert result is None
    assert called is False
    db.rollback.assert_awaited()
    warning.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_input_remains_presentation_owned_and_does_not_write(monkeypatch):
    state = _state(
        state_name=CalculatorStates.waiting_contract_price.state,
        data={"calculator_case_id": 42},
    )
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    called = False

    async def handler(_event, _data):
        nonlocal called
        called = True
        return "validation-rendered"

    result = await calculator_durable.DurableCalculatorIntakeMiddleware()(
        handler,
        _message("not-a-price"),
        {"state": state, "db": db},
    )

    assert result == "validation-rendered"
    assert called is True
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_postgres_intake_is_never_overwritten_from_redis_cutover(
    monkeypatch,
):
    state = _state(
        state_name=CalculatorStates.waiting_planned_transfer_date.state,
        data={
            "calculator_case_id": 42,
            "contract_price": "1000000",
        },
    )
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    durable_intake = object()
    service = SimpleNamespace(
        get=AsyncMock(return_value=durable_intake),
        sync_from_draft=AsyncMock(),
        save_planned_date=AsyncMock(),
    )
    monkeypatch.setattr(
        calculator_durable,
        "CalculationIntakeService",
        lambda _db: service,
    )

    async def owned_case(*_args, **_kwargs):
        return SimpleNamespace(status="CALCULATOR_STARTED", route=None)

    monkeypatch.setattr(calculator_durable, "_owned_calculator_case", owned_case)

    async def handler(_event, _data):
        return "ok"

    result = await calculator_durable.DurableCalculatorIntakeMiddleware()(
        handler,
        _message("15.01.2026"),
        {"state": state, "db": db},
    )

    assert result == "ok"
    service.sync_from_draft.assert_not_awaited()
    service.save_planned_date.assert_awaited_once()
    db.commit.assert_awaited_once()
