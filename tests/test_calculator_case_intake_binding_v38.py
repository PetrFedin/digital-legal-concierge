from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot import context as context_module
from app.bot.context import BotContextService
from app.domain.statuses.case_statuses import CaseStatus


@pytest.mark.asyncio
async def test_calculator_case_creation_ensures_durable_intake_before_caller_commit(
    monkeypatch,
):
    service = BotContextService(db=SimpleNamespace())
    service.case_service = SimpleNamespace(
        create_case_for_operation=AsyncMock(
            return_value=SimpleNamespace(
                id=42,
                status=CaseStatus.CALCULATOR_STARTED.value,
            )
        )
    )
    intake = SimpleNamespace(ensure=AsyncMock())
    monkeypatch.setattr(
        context_module,
        "CalculationIntakeService",
        lambda _db: intake,
    )

    case = await service.create_case_from_callback(
        user=SimpleNamespace(id=7),
        callback=SimpleNamespace(id="telegram-callback-1"),
        purpose="calculator_start",
        status=CaseStatus.CALCULATOR_STARTED,
        title="Обращение по ДДУ",
    )

    assert case.id == 42
    intake.ensure.assert_awaited_once_with(case_id=42)


@pytest.mark.asyncio
async def test_non_calculator_case_creation_does_not_create_calculator_intake(monkeypatch):
    service = BotContextService(db=SimpleNamespace())
    service.case_service = SimpleNamespace(
        create_case_for_operation=AsyncMock(
            return_value=SimpleNamespace(id=77, status="NEW")
        )
    )
    intake = SimpleNamespace(ensure=AsyncMock())
    monkeypatch.setattr(
        context_module,
        "CalculationIntakeService",
        lambda _db: intake,
    )

    await service.create_case_from_callback(
        user=SimpleNamespace(id=7),
        callback=SimpleNamespace(id="telegram-callback-2"),
        purpose="consultation_start",
        status="NEW",
        title="Консультация",
    )

    intake.ensure.assert_not_awaited()
