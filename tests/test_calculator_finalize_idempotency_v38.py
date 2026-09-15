from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.calculator.calculator_service import (
    CalculatorRouteEligibilityError,
    CalculatorService,
)


def _persisted_calculation(*, case_id: int = 42):
    return SimpleNamespace(
        id=501,
        case_id=case_id,
        contract_price=Decimal("8500000.00"),
        planned_transfer_date=date(2026, 1, 15),
        calculation_date=date(2026, 9, 16),
        object_transferred=False,
        actual_transfer_date=None,
        delay_days=10,
        delay_days_total=10,
        delay_days_chargeable=8,
        moratorium_days=2,
        key_rate=None,
        consumer_multiplier=Decimal("2.0000"),
        client_type="consumer",
        penalty_amount=Decimal("12345.67"),
        formula_version="rule:legal-r1",
        rule_revision_id=7,
        rule_revision_key="legal-r1",
        rule_snapshot_sha256="a" * 64,
        rule_snapshot={"schema_version": 1},
        applied_segments=[
            {
                "start": "2026-01-16",
                "end": "2026-01-23",
                "days": 8,
                "rate": "0.10",
            }
        ],
        is_preliminary=True,
    )


@pytest.mark.asyncio
async def test_retry_after_committed_completion_returns_same_result_without_recalculation():
    persisted = _persisted_calculation()
    db = SimpleNamespace(
        get=AsyncMock(return_value=persisted),
    )
    service = CalculatorService(db)
    service.intakes.get_for_update = AsyncMock(
        return_value=SimpleNamespace(completed_calculation_id=501)
    )
    service.rule_revisions.resolve = AsyncMock()
    service.calculator.calculate = AsyncMock()

    result = await service.calculate_and_save(
        case=SimpleNamespace(id=42),
        contract_price=Decimal("1"),
        planned_transfer_date=date(2026, 1, 1),
        calculation_date=date(2026, 9, 16),
        object_transferred=False,
    )

    assert result.penalty_amount == Decimal("12345.67")
    assert result.rule_revision_id == 7
    assert result.rule_revision_key == "legal-r1"
    assert result.delay_days_total == 10
    assert result.delay_days_chargeable == 8
    assert result.moratorium_days == 2
    service.rule_revisions.resolve.assert_not_awaited()
    service.calculator.calculate.assert_not_called()


@pytest.mark.asyncio
async def test_completed_intake_cannot_return_calculation_from_another_case():
    persisted = _persisted_calculation(case_id=999)
    db = SimpleNamespace(get=AsyncMock(return_value=persisted))
    service = CalculatorService(db)
    service.intakes.get_for_update = AsyncMock(
        return_value=SimpleNamespace(completed_calculation_id=501)
    )

    with pytest.raises(CalculatorRouteEligibilityError, match="недоступный расчёт"):
        await service.calculate_and_save(
            case=SimpleNamespace(id=42),
            contract_price=Decimal("8500000"),
            planned_transfer_date=date(2026, 1, 15),
            calculation_date=date(2026, 9, 16),
            object_transferred=False,
        )
