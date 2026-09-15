from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calculation_intake import CalculationIntake


class CalculationIntakeError(ValueError):
    """Durable calculator intake could not be resolved or updated safely."""


def _positive_decimal(value: object) -> Decimal:
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise CalculationIntakeError("Стоимость должна быть числом") from error
    if not amount.is_finite() or amount <= 0:
        raise CalculationIntakeError("Стоимость должна быть положительным числом")
    return amount


def _draft_date(value: object, title: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as error:
        raise CalculationIntakeError(f"{title}: некорректная дата") from error


class CalculationIntakeService:
    """Own accepted questionnaire facts before a completed Calculation exists.

    Telegram FSM is a presentation/conversation cache. The Case-bound row is the
    durable source for accepted questionnaire answers. Completed Calculation
    rows remain immutable historical calculation facts.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, *, case_id: int) -> CalculationIntake | None:
        statement = select(CalculationIntake).where(
            CalculationIntake.case_id == int(case_id)
        )
        return (await self.db.execute(statement)).scalar_one_or_none()

    async def ensure(self, *, case_id: int) -> CalculationIntake:
        normalized_case_id = int(case_id)
        if normalized_case_id <= 0:
            raise CalculationIntakeError("case_id должен быть положительным")
        intake = await self.get(case_id=normalized_case_id)
        if intake is not None:
            return intake
        intake = CalculationIntake(case_id=normalized_case_id, current_step="price")
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
        amount = _positive_decimal(contract_price)
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

    async def sync_from_draft(
        self,
        *,
        case_id: int,
        data: dict[str, Any],
        today: date,
    ) -> CalculationIntake:
        """Replace the active intake with one validated FSM snapshot.

        The caller must invoke this only after the canonical Telegram handler has
        accepted the event for the exact Case. Missing downstream keys are
        intentional invalidation (for example after changing the DDU date), not
        permission to retain stale values.
        """

        intake = await self.ensure(case_id=case_id)

        raw_price = data.get("contract_price")
        if raw_price in (None, ""):
            intake.contract_price = None
            intake.planned_transfer_date = None
            intake.object_transferred = None
            intake.actual_transfer_date = None
            intake.calculation_date = None
            intake.completed_at = None
            intake.current_step = "price"
            await self.db.flush()
            return intake

        intake.contract_price = _positive_decimal(raw_price)

        raw_planned = data.get("planned_transfer_date")
        if raw_planned in (None, ""):
            intake.planned_transfer_date = None
            intake.object_transferred = None
            intake.actual_transfer_date = None
            intake.calculation_date = None
            intake.completed_at = None
            intake.current_step = "planned_date"
            await self.db.flush()
            return intake

        planned = _draft_date(raw_planned, "Договорная дата передачи")
        intake.planned_transfer_date = planned
        intake.calculation_date = None
        intake.completed_at = None

        if planned > today:
            intake.object_transferred = None
            intake.actual_transfer_date = None
            intake.current_step = "future_date"
            await self.db.flush()
            return intake

        if "object_transferred" not in data:
            intake.object_transferred = None
            intake.actual_transfer_date = None
            intake.current_step = "transfer_status"
            await self.db.flush()
            return intake

        transferred = bool(data.get("object_transferred"))
        intake.object_transferred = transferred
        if not transferred:
            intake.actual_transfer_date = None
            intake.current_step = "ready"
            await self.db.flush()
            return intake

        raw_actual = data.get("actual_transfer_date")
        if raw_actual in (None, ""):
            intake.actual_transfer_date = None
            intake.current_step = "actual_date"
            await self.db.flush()
            return intake

        actual = _draft_date(raw_actual, "Фактическая дата передачи")
        if actual > today:
            raise CalculationIntakeError(
                "Фактическая дата передачи не может быть в будущем"
            )
        # actual < planned is deliberately valid: it is an on-time/early factual
        # transfer and later produces zero delay under the functional spec.
        intake.actual_transfer_date = actual
        intake.current_step = "ready"
        await self.db.flush()
        return intake

    async def complete_from_result(
        self,
        *,
        case_id: int,
        result: Any,
    ) -> CalculationIntake:
        """Persist the exact completed questionnaire inputs in the Case card."""

        intake = await self.ensure(case_id=case_id)
        intake.contract_price = _positive_decimal(result.contract_price)
        intake.planned_transfer_date = result.planned_transfer_date
        intake.object_transferred = bool(result.object_transferred)
        intake.actual_transfer_date = result.actual_transfer_date
        intake.calculation_date = result.calculation_date
        intake.completed_at = datetime.now(timezone.utc)
        intake.current_step = "completed"
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

    async def draft_data(
        self,
        *,
        case_id: int,
        include_completed: bool = False,
    ) -> dict | None:
        intake = await self.get(case_id=case_id)
        if intake is None:
            return None
        if intake.completed_at is not None and not include_completed:
            return None
        return self.as_draft_data(intake)


__all__ = ["CalculationIntakeError", "CalculationIntakeService"]
