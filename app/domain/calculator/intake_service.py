from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calculation_intake import CalculationIntake


class CalculationIntakeError(ValueError):
    """Durable calculator intake could not be resolved or updated safely."""


class CalculationIntakeService:
    """Own accepted questionnaire facts before a completed Calculation exists."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, *, case_id: int) -> CalculationIntake | None:
        statement = select(CalculationIntake).where(
            CalculationIntake.case_id == int(case_id)
        )
        return (await self.db.execute(statement)).scalar_one_or_none()

    async def ensure(self, *, case_id: int) -> CalculationIntake:
        intake = await self.get(case_id=case_id)
        if intake is not None:
            return intake
        intake = CalculationIntake(case_id=int(case_id), current_step="price")
        self.db.add(intake)
        await self.db.flush()
        return intake

    async def reset(self, *, case_id: int) -> CalculationIntake:
        intake = await self.ensure(case_id=case_id)
        intake.contract_price = None
        intake.planned_transfer_date = None
        intake.object_transferred = None
        intake.actual_transfer_date = None
        intake.calculation_date = None
        intake.completed_at = None
        intake.current_step = "price"
        await self.db.flush()
        return intake

    async def save_price(
        self,
        *,
        case_id: int,
        contract_price: Decimal,
    ) -> CalculationIntake:
        amount = Decimal(contract_price)
        if not amount.is_finite() or amount <= 0:
            raise CalculationIntakeError("Стоимость должна быть положительным числом")
        intake = await self.ensure(case_id=case_id)
        intake.contract_price = amount
        intake.completed_at = None
        intake.calculation_date = None
        intake.current_step = "planned_date"
        await self.db.flush()
        return intake

    async def save_planned_date(
        self,
        *,
        case_id: int,
        planned_transfer_date: date,
        today: date,
    ) -> CalculationIntake:
        intake = await self.ensure(case_id=case_id)
        intake.planned_transfer_date = planned_transfer_date
        # A changed upstream contractual date invalidates transfer answers.
        intake.object_transferred = None
        intake.actual_transfer_date = None
        intake.completed_at = None
        intake.calculation_date = None
        intake.current_step = (
            "future_date" if planned_transfer_date > today else "transfer_status"
        )
        await self.db.flush()
        return intake

    async def save_transfer_status(
        self,
        *,
        case_id: int,
        object_transferred: bool,
    ) -> CalculationIntake:
        intake = await self.ensure(case_id=case_id)
        if intake.contract_price is None or intake.planned_transfer_date is None:
            raise CalculationIntakeError(
                "Нельзя сохранить статус передачи до стоимости и договорной даты"
            )
        intake.object_transferred = bool(object_transferred)
        intake.actual_transfer_date = None
        intake.completed_at = None
        intake.calculation_date = None
        intake.current_step = "actual_date" if object_transferred else "ready"
        await self.db.flush()
        return intake

    async def save_actual_date(
        self,
        *,
        case_id: int,
        actual_transfer_date: date,
        today: date,
    ) -> CalculationIntake:
        intake = await self.ensure(case_id=case_id)
        if intake.object_transferred is not True:
            raise CalculationIntakeError(
                "Фактическая дата допустима только для переданного объекта"
            )
        if actual_transfer_date > today:
            raise CalculationIntakeError(
                "Фактическая дата передачи не может быть в будущем"
            )
        intake.actual_transfer_date = actual_transfer_date
        intake.completed_at = None
        intake.calculation_date = None
        intake.current_step = "ready"
        await self.db.flush()
        return intake

    async def mark_completed(
        self,
        *,
        case_id: int,
        calculation_date: date,
    ) -> CalculationIntake:
        intake = await self.ensure(case_id=case_id)
        intake.calculation_date = calculation_date
        intake.completed_at = datetime.now(timezone.utc)
        intake.current_step = "completed"
        await self.db.flush()
        return intake

    def as_draft_data(self, intake: CalculationIntake) -> dict:
        data: dict = {"calculator_case_id": int(intake.case_id)}
        if intake.contract_price is not None:
            data["contract_price"] = str(intake.contract_price)
        if intake.planned_transfer_date is not None:
            data["planned_transfer_date"] = intake.planned_transfer_date.isoformat()
        if intake.object_transferred is not None:
            data["object_transferred"] = bool(intake.object_transferred)
        if intake.actual_transfer_date is not None:
            data["actual_transfer_date"] = intake.actual_transfer_date.isoformat()
        return data

    async def draft_data(self, *, case_id: int) -> dict | None:
        intake = await self.get(case_id=case_id)
        if intake is None:
            return None
        return self.as_draft_data(intake)


__all__ = ["CalculationIntakeError", "CalculationIntakeService"]
