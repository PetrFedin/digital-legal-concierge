from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.calculator.intake_service import (
    INTAKE_COMPLETED,
    INTAKE_IN_PROGRESS,
    CalculationIntakeError,
    CalculationIntakeService,
)
from app.models.calculation_intake import CalculationIntake


@pytest.fixture
def service_and_intake():
    db = SimpleNamespace(flush=AsyncMock())
    service = CalculationIntakeService(db)
    intake = CalculationIntake(
        case_id=42,
        current_step="price",
        status=INTAKE_IN_PROGRESS,
        version=1,
    )
    service.ensure = AsyncMock(return_value=intake)
    service.get_for_update = AsyncMock(return_value=intake)
    return service, intake, db


@pytest.mark.asyncio
async def test_draft_snapshot_persists_each_accepted_fact(service_and_intake):
    service, intake, _db = service_and_intake

    await service.sync_from_draft(
        case_id=42,
        data={
            "calculator_case_id": 42,
            "contract_price": "8500000.00",
            "planned_transfer_date": "2026-01-15",
            "object_transferred": True,
            "actual_transfer_date": "2026-01-10",
        },
        today=date(2026, 9, 15),
    )

    assert intake.contract_price == Decimal("8500000.00")
    assert intake.planned_transfer_date == date(2026, 1, 15)
    assert intake.object_transferred is True
    # Early transfer is factual data, not a validation error.
    assert intake.actual_transfer_date == date(2026, 1, 10)
    assert intake.current_step == "ready"
    assert intake.completed_at is None
    assert intake.status == INTAKE_IN_PROGRESS
    assert intake.version == 2


@pytest.mark.asyncio
async def test_changed_planned_date_clears_downstream_transfer_answers(service_and_intake):
    service, intake, _db = service_and_intake
    intake.contract_price = Decimal("8500000")
    intake.planned_transfer_date = date(2026, 1, 15)
    intake.object_transferred = True
    intake.actual_transfer_date = date(2026, 1, 20)

    await service.sync_from_draft(
        case_id=42,
        data={
            "calculator_case_id": 42,
            "contract_price": "8500000",
            "planned_transfer_date": "2026-02-01",
        },
        today=date(2026, 9, 15),
    )

    assert intake.planned_transfer_date == date(2026, 2, 1)
    assert intake.object_transferred is None
    assert intake.actual_transfer_date is None
    assert intake.current_step == "transfer_status"


@pytest.mark.asyncio
async def test_future_contract_date_is_durable_informational_boundary(service_and_intake):
    service, intake, _db = service_and_intake

    await service.sync_from_draft(
        case_id=42,
        data={
            "calculator_case_id": 42,
            "contract_price": "8500000",
            "planned_transfer_date": "2026-10-01",
            # Any stale downstream keys must not survive this boundary.
            "object_transferred": True,
            "actual_transfer_date": "2026-09-01",
        },
        today=date(2026, 9, 15),
    )

    assert intake.planned_transfer_date == date(2026, 10, 1)
    assert intake.object_transferred is None
    assert intake.actual_transfer_date is None
    assert intake.current_step == "future_date"


@pytest.mark.asyncio
async def test_restart_snapshot_resets_only_active_intake(service_and_intake):
    service, intake, _db = service_and_intake
    intake.contract_price = Decimal("8500000")
    intake.planned_transfer_date = date(2026, 1, 15)
    intake.object_transferred = False
    intake.status = INTAKE_COMPLETED
    intake.completed_calculation_id = 77

    await service.reset(case_id=42)

    assert intake.contract_price is None
    assert intake.planned_transfer_date is None
    assert intake.object_transferred is None
    assert intake.actual_transfer_date is None
    assert intake.current_step == "price"
    assert intake.status == INTAKE_IN_PROGRESS
    assert intake.completed_calculation_id is None


@pytest.mark.asyncio
async def test_completed_result_copies_exact_input_facts_and_seals_calculation(service_and_intake):
    service, intake, _db = service_and_intake
    result = SimpleNamespace(
        contract_price=Decimal("8500000.00"),
        planned_transfer_date=date(2026, 1, 15),
        object_transferred=False,
        actual_transfer_date=None,
        calculation_date=date(2026, 9, 15),
    )

    await service.complete_from_result(
        case_id=42,
        result=result,
        calculation_id=501,
    )

    assert intake.contract_price == Decimal("8500000.00")
    assert intake.planned_transfer_date == date(2026, 1, 15)
    assert intake.object_transferred is False
    assert intake.actual_transfer_date is None
    assert intake.calculation_date == date(2026, 9, 15)
    assert intake.completed_at is not None
    assert intake.current_step == "completed"
    assert intake.status == INTAKE_COMPLETED
    assert intake.completed_calculation_id == 501
    assert intake.version == 2


@pytest.mark.asyncio
async def test_completed_intake_cannot_be_rebound_to_different_calculation(service_and_intake):
    service, intake, _db = service_and_intake
    intake.status = INTAKE_COMPLETED
    intake.completed_calculation_id = 501
    intake.completed_at = SimpleNamespace()
    result = SimpleNamespace(
        contract_price=Decimal("8500000.00"),
        planned_transfer_date=date(2026, 1, 15),
        object_transferred=False,
        actual_transfer_date=None,
        calculation_date=date(2026, 9, 15),
    )

    with pytest.raises(CalculationIntakeError, match="уже завершён другим расчётом"):
        await service.complete_from_result(
            case_id=42,
            result=result,
            calculation_id=502,
        )


@pytest.mark.asyncio
async def test_future_actual_date_is_not_durable(service_and_intake):
    service, _intake, _db = service_and_intake

    with pytest.raises(CalculationIntakeError, match="не может быть в будущем"):
        await service.sync_from_draft(
            case_id=42,
            data={
                "calculator_case_id": 42,
                "contract_price": "8500000",
                "planned_transfer_date": "2026-01-15",
                "object_transferred": True,
                "actual_transfer_date": "2026-09-16",
            },
            today=date(2026, 9, 15),
        )


def test_intake_reconstructs_fsm_without_redis(service_and_intake):
    service, intake, _db = service_and_intake
    intake.contract_price = Decimal("8500000.00")
    intake.planned_transfer_date = date(2026, 1, 15)
    intake.object_transferred = True
    intake.actual_transfer_date = date(2026, 1, 10)

    assert service.as_draft_data(intake) == {
        "calculator_case_id": 42,
        "contract_price": "8500000.00",
        "planned_transfer_date": "2026-01-15",
        "object_transferred": True,
        "actual_transfer_date": "2026-01-10",
    }
