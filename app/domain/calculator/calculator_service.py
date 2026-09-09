from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.calculator.penalty_calculator import (
    PenaltyCalculationInput,
    PenaltyCalculator,
)
from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.statuses.case_statuses import CaseStatus
from app.models.calculation import Calculation
from app.models.case import Case


_CALCULATION_PHASE_STATUSES = {
    CaseStatus.NEW,
    CaseStatus.CALCULATOR_STARTED,
    CaseStatus.CALCULATED,
    CaseStatus.CLIENT_DECISION,
}


class CalculatorRouteEligibilityError(ValueError):
    """Raised when a calculator outcome cannot enter the requested legal route."""


class CalculatorService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.calculator = PenaltyCalculator()

    async def latest_calculation_for_case(self, *, case_id: int) -> Calculation | None:
        statement = (
            select(Calculation)
            .where(Calculation.case_id == int(case_id))
            .order_by(Calculation.created_at.desc(), Calculation.id.desc())
            .limit(1)
        )
        return (await self.db.execute(statement)).scalar_one_or_none()

    async def require_m1_eligible_calculation(self, *, case_id: int) -> Calculation:
        """Require the current Case outcome to contain a positive delay/amount.

        Calculator result buttons are Telegram messages and may be replayed after
        a later recalculation. Eligibility therefore comes from the newest stored
        Calculation, never from the button that happened to be clicked. A
        zero-delay/zero-amount outcome is an informational completion/M2 outcome
        under the approved functional specification and cannot be promoted to M1
        by a stale positive-result button.
        """

        calculation = await self.latest_calculation_for_case(case_id=case_id)
        if calculation is None:
            raise CalculatorRouteEligibilityError(
                "Для продолжения М1 нужен сохранённый предварительный расчёт."
            )
        delay_days = int(calculation.delay_days or 0)
        penalty_amount = Decimal(calculation.penalty_amount or 0)
        if delay_days <= 0 or penalty_amount <= 0:
            raise CalculatorRouteEligibilityError(
                "По последнему расчёту просрочка или положительная сумма неустойки отсутствует."
            )
        return calculation

    async def calculate_and_save(
        self,
        *,
        case: Case,
        contract_price: Decimal,
        planned_transfer_date: date,
        calculation_date: date,
        object_transferred: bool,
        actual_transfer_date: date | None = None,
        key_rate: Decimal | None = None,
        consumer_multiplier: Decimal = Decimal("2"),
    ):
        effective_rate = (
            Decimal(str(settings.legal_key_rate))
            if key_rate is None
            else Decimal(str(key_rate))
        )
        result = self.calculator.calculate(
            PenaltyCalculationInput(
                contract_price=contract_price,
                planned_transfer_date=planned_transfer_date,
                calculation_date=calculation_date,
                object_transferred=object_transferred,
                actual_transfer_date=actual_transfer_date,
                key_rate=effective_rate,
                consumer_multiplier=consumer_multiplier,
            )
        )

        # Every completed calculation is a historical fact. Never overwrite a
        # previous Calculation: the approved model is Case -> Calculation 1:N,
        # and readers determine the current value by newest created_at/id.
        calculation = Calculation(
            case_id=case.id,
            contract_price=result.contract_price,
            planned_transfer_date=result.planned_transfer_date,
            calculation_date=result.calculation_date,
            actual_transfer_date=result.actual_transfer_date,
            object_transferred=result.object_transferred,
            delay_days=result.delay_days,
            key_rate=result.key_rate,
            consumer_multiplier=result.consumer_multiplier,
            penalty_amount=result.penalty_amount,
            formula_version=result.formula_version,
            is_preliminary=True,
        )
        self.db.add(calculation)

        # Recalculation must not silently move a case backwards from an active
        # legal or consultation stage. Only the initial calculator phase changes
        # the workflow status.
        if CaseStatus(str(case.status)) in _CALCULATION_PHASE_STATUSES:
            await CaseService(self.db).change_status(
                case=case,
                next_status=CaseStatus.CALCULATED,
                actor_type="client",
                actor_id=case.client_id,
                comment="Предварительный расчёт сохранён",
            )

        await self.db.flush()
        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=case.client_id,
            case_id=case.id,
            action="CALCULATION_COMPLETED",
            new_value={
                "calculation_id": calculation.id,
                "penalty_amount": str(result.penalty_amount),
                "delay_days": result.delay_days,
                "calculation_date": result.calculation_date.isoformat(),
                "key_rate": str(result.key_rate),
                "consumer_multiplier": str(result.consumer_multiplier),
                "formula_version": result.formula_version,
            },
        )
        await self.db.flush()
        return result