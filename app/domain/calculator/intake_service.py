from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calculation_intake import CalculationIntake

INTAKE_IN_PROGRESS = "IN_PROGRESS"
INTAKE_COMPLETED = "COMPLETED"


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


def _next_version(intake: CalculationIntake) -> int:
    return max(int(intake.version or 0), 0) + 1


class CalculationIntakeService:
    """Own accepted questionnaire facts before a completed Calculation exists.

    Telegram FSM is a presentation/conversation cache. The Case-bound row is the
    durable source for accepted questionnaire answers. Every accepted mutation
    increments ``version``. Completion seals the exact Calculation id, so a
    Telegram retry can return the same historical result rather than append a
    duplicate.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, *, case_id: int) -> CalculationIntake | None:
        statement = select(CalculationIntake).where(
            CalculationIntake.case_id == int(case_id)
        )
        return (await self.db.execute(statement)).scalar_one_or_none()

    async def get_for_update(self, *, case_id: int) -> CalculationIntake | None:
        statement = (
            select(CalculationIntake)
            .where(CalculationIntake.case_id == int(case_id))
            .with_for_update()
        )
        return (await self.db.execute(statement)).scalar_one_or_none()

    async def ensure(self, *, case_id: int) -> CalculationIntake:
        normalized_case_id = int(case_id)
        if normalized_case_id <= 0:
            raise CalculationIntakeError("case_id должен быть положительным")
        intake = await self.get(case_id=normalized_case_id)
        if intake is not None:
            return intake
        intake = CalculationIntake(
            case_id=normalized_case_id,
            current_step="price",
            status=INTAKE_IN_PROGRESS,
            version=1,
        )
        self.db.add(intake)
        await self.db.flush()
        return intake

    async def _mutable(self, *, case_id: int) -> CalculationIntake:
        intake = await self.get_for_update(case_id=case_id)
        if intake is not None:
            return intake
        return await self.ensure(case_id=case_id)

    @staticmethod
    def _reopen(intake: CalculationIntake) -> None:
        intake.status = INTAKE_IN_PROGRESS
        intake.completed_at = None
        intake.completed_calculation_id = None
        intake.calculation_date = None
        intake.version = _next_version(intake)

    async def reset(self, *, case_id: int) -> CalculationIntake:
        intake = await self._mutable(case_id=case_id)
        self._reopen(intake)
        intake.contract_price = None
        intake.planned_transfer_date = None
        intake.object_transferred = None
        intake.actual_transfer_date = None
        intake.client_type = None
        intake.unique_object = None
        intake.manual_review_flags = None
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
        intake = await self._mutable(case_id=case_id)
        self._reopen(intake)
        intake.contract_price = amount
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
        intake = await self._mutable(case_id=case_id)
        self._reopen(intake)
        intake.planned_transfer_date = planned_transfer_date
        # A changed upstream contractual date invalidates transfer answers.
        intake.object_transferred = None
        intake.actual_transfer_date = None
        intake.client_type = None
        intake.unique_object = None
        intake.manual_review_flags = None
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
        intake = await self._mutable(case_id=case_id)
        if intake.contract_price is None or intake.planned_transfer_date is None:
            raise CalculationIntakeError(
                "Нельзя сохранить статус передачи до стоимости и договорной даты"
            )
        self._reopen(intake)
        intake.object_transferred = bool(object_transferred)
        intake.actual_transfer_date = None
        intake.client_type = None
        intake.unique_object = None
        intake.manual_review_flags = None
        intake.current_step = "actual_date" if object_transferred else "client_type"
        await self.db.flush()
        return intake

    async def save_actual_date(
        self,
        *,
        case_id: int,
        actual_transfer_date: date,
        today: date,
    ) -> CalculationIntake:
        intake = await self._mutable(case_id=case_id)
        if intake.object_transferred is not True:
            raise CalculationIntakeError(
                "Фактическая дата допустима только для переданного объекта"
            )
        if actual_transfer_date > today:
            raise CalculationIntakeError(
                "Фактическая дата передачи не может быть в будущем"
            )
        self._reopen(intake)
        intake.actual_transfer_date = actual_transfer_date
        intake.client_type = None
        intake.unique_object = None
        intake.manual_review_flags = None
        intake.current_step = "client_type"
        await self.db.flush()
        return intake

    async def save_client_type(
        self,
        *,
        case_id: int,
        client_type: str,
    ) -> CalculationIntake:
        value = str(client_type or "").strip().lower()
        if value not in {"consumer", "other"}:
            raise CalculationIntakeError("Неизвестный тип участника расчёта")
        intake = await self._mutable(case_id=case_id)
        if (
            intake.contract_price is None
            or intake.planned_transfer_date is None
            or intake.object_transferred is None
            or (intake.object_transferred and intake.actual_transfer_date is None)
        ):
            raise CalculationIntakeError(
                "Тип участника нельзя сохранить до основных фактов передачи"
            )
        self._reopen(intake)
        intake.client_type = value
        intake.unique_object = None
        intake.manual_review_flags = None
        intake.current_step = "unique_object"
        await self.db.flush()
        return intake

    async def save_unique_object(
        self,
        *,
        case_id: int,
        unique_object: bool,
    ) -> CalculationIntake:
        intake = await self._mutable(case_id=case_id)
        if intake.client_type not in {"consumer", "other"}:
            raise CalculationIntakeError(
                "Признак уникального объекта нельзя сохранить до типа участника"
            )
        self._reopen(intake)
        intake.unique_object = bool(unique_object)
        intake.current_step = "ready"
        await self.db.flush()
        return intake

    async def mark_unique_object_unknown(
        self,
        *,
        case_id: int,
    ) -> CalculationIntake:
        intake = await self._mutable(case_id=case_id)
        if intake.client_type not in {"consumer", "other"}:
            raise CalculationIntakeError(
                "Неопределённый статус объекта нельзя сохранить до типа участника"
            )
        self._reopen(intake)
        intake.unique_object = None
        intake.manual_review_flags = ["unique_object_unknown"]
        intake.current_step = "manual_review"
        await self.db.flush()
        return intake

    async def set_manual_review_flags(
        self,
        *,
        case_id: int,
        flags: list[str] | tuple[str, ...],
    ) -> CalculationIntake:
        intake = await self._mutable(case_id=case_id)
        self._reopen(intake)
        intake.manual_review_flags = sorted(
            {str(item).strip() for item in flags if str(item).strip()}
        )
        await self.db.flush()
        return intake

    async def sync_from_draft(
        self,
        *,
        case_id: int,
        data: dict[str, Any],
        today: date,
    ) -> CalculationIntake:
        """Import one validated legacy FSM snapshot when no durable copy exists.

        Normal PM-017 runtime writes individual accepted facts before rendering.
        This method remains for the one-time cutover/recovery path only. Callers
        must never use it to overwrite an already-authoritative PostgreSQL row
        with a newer Redis snapshot.
        """

        intake = await self._mutable(case_id=case_id)
        self._reopen(intake)
        intake.client_type = None
        intake.unique_object = None
        intake.manual_review_flags = None

        raw_price = data.get("contract_price")
        if raw_price in (None, ""):
            intake.contract_price = None
            intake.planned_transfer_date = None
            intake.object_transferred = None
            intake.actual_transfer_date = None
            intake.current_step = "price"
            await self.db.flush()
            return intake

        intake.contract_price = _positive_decimal(raw_price)

        raw_planned = data.get("planned_transfer_date")
        if raw_planned in (None, ""):
            intake.planned_transfer_date = None
            intake.object_transferred = None
            intake.actual_transfer_date = None
            intake.current_step = "planned_date"
            await self.db.flush()
            return intake

        planned = _draft_date(raw_planned, "Договорная дата передачи")
        intake.planned_transfer_date = planned

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
        if transferred:
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
            intake.actual_transfer_date = actual
        else:
            intake.actual_transfer_date = None

        raw_client_type = str(data.get("client_type") or "").strip().lower()
        if raw_client_type not in {"consumer", "other"}:
            intake.current_step = "client_type"
            await self.db.flush()
            return intake
        intake.client_type = raw_client_type

        if "unique_object" not in data:
            intake.current_step = "unique_object"
            await self.db.flush()
            return intake
        intake.unique_object = bool(data.get("unique_object"))
        flags = data.get("manual_review_flags") or []
        if not isinstance(flags, (list, tuple)):
            raise CalculationIntakeError("manual_review_flags должен быть списком")
        intake.manual_review_flags = sorted(
            {str(item).strip() for item in flags if str(item).strip()}
        )
        intake.current_step = "ready"
        await self.db.flush()
        return intake

    async def complete_from_result(
        self,
        *,
        case_id: int,
        result: Any,
        calculation_id: int,
    ) -> CalculationIntake:
        """Seal exact completed inputs and the immutable Calculation identity."""

        intake = await self._mutable(case_id=case_id)
        normalized_calculation_id = int(calculation_id)
        if normalized_calculation_id <= 0:
            raise CalculationIntakeError("calculation_id должен быть положительным")
        if intake.status == INTAKE_COMPLETED and intake.completed_calculation_id:
            if int(intake.completed_calculation_id) == normalized_calculation_id:
                return intake
            raise CalculationIntakeError(
                "Черновик уже завершён другим расчётом; сначала требуется явный пересчёт"
            )

        intake.contract_price = _positive_decimal(result.contract_price)
        intake.planned_transfer_date = result.planned_transfer_date
        intake.object_transferred = bool(result.object_transferred)
        intake.actual_transfer_date = result.actual_transfer_date
        intake.client_type = str(result.client_type)
        intake.unique_object = bool(result.unique_object)
        intake.manual_review_flags = list(result.manual_review_reasons or [])
        intake.calculation_date = result.calculation_date
        intake.completed_calculation_id = normalized_calculation_id
        intake.completed_at = datetime.now(timezone.utc)
        intake.current_step = "completed"
        intake.status = INTAKE_COMPLETED
        intake.version = _next_version(intake)
        await self.db.flush()
        return intake

    async def mark_completed(
        self,
        *,
        case_id: int,
        calculation_date: date,
        calculation_id: int,
    ) -> CalculationIntake:
        intake = await self._mutable(case_id=case_id)
        normalized_calculation_id = int(calculation_id)
        if normalized_calculation_id <= 0:
            raise CalculationIntakeError("calculation_id должен быть положительным")
        intake.calculation_date = calculation_date
        intake.completed_calculation_id = normalized_calculation_id
        intake.completed_at = datetime.now(timezone.utc)
        intake.current_step = "completed"
        intake.status = INTAKE_COMPLETED
        intake.version = _next_version(intake)
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
        if intake.client_type:
            data["client_type"] = str(intake.client_type)
        if intake.unique_object is not None:
            data["unique_object"] = bool(intake.unique_object)
        if intake.manual_review_flags:
            data["manual_review_flags"] = list(intake.manual_review_flags)
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
        if (
            intake.status == INTAKE_COMPLETED
            or intake.completed_at is not None
        ) and not include_completed:
            return None
        return self.as_draft_data(intake)


__all__ = [
    "INTAKE_COMPLETED",
    "INTAKE_IN_PROGRESS",
    "CalculationIntakeError",
    "CalculationIntakeService",
]
