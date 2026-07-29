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


class CalculatorService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.calculator = PenaltyCalculator()

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

        query = await self.db.execute(
            select(Calculation).where(Calculation.case_id == case.id)
        )
        calculation = query.scalars().first()
        if calculation is None:
            calculation = Calculation(case_id=case.id)
            self.db.add(calculation)

        calculation.contract_price = result.contract_price
        calculation.planned_transfer_date = result.planned_transfer_date
        calculation.calculation_date = result.calculation_date
        calculation.actual_transfer_date = result.actual_transfer_date
        calculation.object_transferred = result.object_transferred
        calculation.delay_days = result.delay_days
        calculation.key_rate = result.key_rate
        calculation.consumer_multiplier = result.consumer_multiplier
        calculation.penalty_amount = result.penalty_amount
        calculation.formula_version = result.formula_version
        calculation.is_preliminary = True

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

        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=case.client_id,
            case_id=case.id,
            action="CALCULATION_COMPLETED",
            new_value={
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
