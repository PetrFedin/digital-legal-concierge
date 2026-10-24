from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.calculator.legal_periods import (
    ManualLegalReviewRequired,
    PRELIMINARY_2026_RULES,
)
from app.domain.calculator.legal_rate_snapshot import key_rate_for_execution_date
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

    def calculate_preview(
        self,
        *,
        contract_price: Decimal,
        planned_transfer_date: date,
        calculation_date: date,
        object_transferred: bool,
        actual_transfer_date: date | None = None,
        key_rate: Decimal | None = None,
        consumer_multiplier: Decimal = Decimal("2"),
    ):
        """Same validated calculation without touching Case or Calculation."""
        # Determine chargeable days before rate selection. For a wholly excluded
        # or zero-delay period, a numeric CBR rate is not needed.
        end_date = (
            actual_transfer_date if object_transferred and actual_transfer_date is not None
            else calculation_date
        )
        period = PRELIMINARY_2026_RULES.breakdown(planned_transfer_date, end_date)
        observed_rate = (
            key_rate_for_execution_date(end_date)
            if period.accrual_days > 0
            else Decimal("0")
        )
        if key_rate is not None and Decimal(str(key_rate)) != observed_rate:
            raise ManualLegalReviewRequired(
                "Переданная ставка не совпадает с подтверждённой ставкой Банка России "
                "на дату исполнения. Требуется повторный расчёт."
            )
        effective_rate = observed_rate
        return self.calculator.calculate(
            PenaltyCalculationInput(
                contract_price=contract_price,
                planned_transfer_date=planned_transfer_date,
                calculation_date=calculation_date,
                object_transferred=object_transferred,
                actual_transfer_date=actual_transfer_date,
                key_rate=effective_rate,
                consumer_multiplier=consumer_multiplier,
                legal_rules=PRELIMINARY_2026_RULES,
            )
        )

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
        result = self.calculate_preview(
            contract_price=contract_price,
            planned_transfer_date=planned_transfer_date,
            calculation_date=calculation_date,
            object_transferred=object_transferred,
            actual_transfer_date=actual_transfer_date,
            key_rate=key_rate,
            consumer_multiplier=consumer_multiplier,
        )

        # Each calculation is an immutable historical snapshot; keep older
        # values for audit and show the newest by id in the case view.
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
                "excluded_days": result.excluded_days,
                "chargeable_days": result.chargeable_days,
                "legal_rules_version": result.legal_rules_version,
                "calculation_date": result.calculation_date.isoformat(),
                "key_rate": str(result.key_rate),
                "consumer_multiplier": str(result.consumer_multiplier),
                "formula_version": result.formula_version,
            },
        )
        await self.db.flush()
        return result
