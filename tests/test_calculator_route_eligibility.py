from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.domain.calculator.calculator_service import (
    CalculatorRouteEligibilityError,
    CalculatorService,
)


@pytest.mark.asyncio
async def test_m1_eligibility_accepts_latest_positive_delay_and_amount(monkeypatch):
    service = CalculatorService(db=None)
    latest = SimpleNamespace(
        id=15,
        delay_days=12,
        penalty_amount=Decimal("14500.00"),
    )

    async def fake_latest(*, case_id: int):
        assert case_id == 77
        return latest

    monkeypatch.setattr(service, "latest_calculation_for_case", fake_latest)

    assert await service.require_m1_eligible_calculation(case_id=77) is latest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "latest",
    [
        None,
        SimpleNamespace(id=16, delay_days=0, penalty_amount=Decimal("0.00")),
        SimpleNamespace(id=17, delay_days=5, penalty_amount=Decimal("0.00")),
    ],
)
async def test_m1_eligibility_rejects_missing_or_zero_latest_outcome(
    monkeypatch,
    latest,
):
    service = CalculatorService(db=None)

    async def fake_latest(*, case_id: int):
        assert case_id == 77
        return latest

    monkeypatch.setattr(service, "latest_calculation_for_case", fake_latest)

    with pytest.raises(CalculatorRouteEligibilityError):
        await service.require_m1_eligible_calculation(case_id=77)
