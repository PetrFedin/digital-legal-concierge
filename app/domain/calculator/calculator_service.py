from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.calculator.intake_service import CalculationIntakeService
from app.domain.calculator.rule_engine import (
    CalculationRuleEngine,
    RuleBasedCalculationInput,
)
from app.domain.calculator.rule_revision_service import CalculationRuleRevisionService
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
        self.calculator = CalculationRuleEngine()
        self.rule_revisions = CalculationRuleRevisionService(db)
        self.intakes = CalculationIntakeService(db)

    async def latest_calculation_for_case(self, *, case_id: int) -> Calculation | None:
        statement = (
            select(Calculation)
            .where(Calculation.case_id == int(case_id))
            .order_by(Calculation.created_at.desc(), Calculation.id.desc())
            .limit(1)
        )
        return (await self.db.execute(statement)).scalar_one_or_none()

    async def require_m1_eligible_calculation(self, *, case_id: int) -> Calculation:
        """Require the current Case outcome to contain a positive charged delay/amount.

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
        delay_days = int(
            calculation.delay_days_chargeable
            if calculation.delay_days_chargeable is not None
            else calculation.delay_days or 0
        )
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
        client_type: str = "consumer",
    ):
        """Calculate only from one approved effective-dated legal rule revision.

        There is deliberately no settings/env/key-rate fallback here. If the
        lawyer-approved rule directory is absent, overlapping or fails integrity
        validation, the calculation fails closed instead of presenting a guessed
        legal amount to the client.
        """

        revision = await self.rule_revisions.resolve(calculation_date=calculation_date)
        result = self.calculator.calculate(
            RuleBasedCalculationInput(
                contract_price=contract_price,
                planned_transfer_date=planned_transfer_date,
                calculation_date=calculation_date,
                object_transferred=object_transferred,
                actual_transfer_date=actual_transfer_date,
                client_type=client_type,
            ),
            rule_revision_id=int(revision.id),
            rule_revision_key=str(revision.revision_key),
            rule_snapshot_sha256=str(revision.rules_sha256),
            rule_snapshot=dict(revision.rules),
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
            delay_days=result.delay_days_chargeable,
            delay_days_total=result.delay_days_total,
            delay_days_chargeable=result.delay_days_chargeable,
            moratorium_days=result.moratorium_days,
            key_rate=result.key_rate,
            consumer_multiplier=result.consumer_multiplier,
            client_type=result.client_type,
            penalty_amount=result.penalty_amount,
            formula_version=result.formula_version,
            rule_revision_id=result.rule_revision_id,
            rule_revision_key=result.rule_revision_key,
            rule_snapshot_sha256=result.rule_snapshot_sha256,
            rule_snapshot=result.rule_snapshot,
            applied_segments=result.applied_segments,
            is_preliminary=True,
        )
        self.db.add(calculation)

        # Completion writes the exact accepted questionnaire facts to the Case
        # card in the same database transaction as the immutable Calculation.
        # Redis can disappear immediately after commit without losing the input
        # that produced this result.
        await self.intakes.complete_from_result(case_id=int(case.id), result=result)

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
        history_value = {
            "calculation_id": calculation.id,
            "penalty_amount": str(result.penalty_amount),
            "delay_days_total": result.delay_days_total,
            "delay_days_chargeable": result.delay_days_chargeable,
            "moratorium_days": result.moratorium_days,
            "calculation_date": result.calculation_date.isoformat(),
            "consumer_multiplier": str(result.consumer_multiplier),
            "client_type": result.client_type,
            "formula_version": result.formula_version,
            "rule_revision_id": result.rule_revision_id,
            "rule_revision_key": result.rule_revision_key,
            "rule_snapshot_sha256": result.rule_snapshot_sha256,
        }
        if result.key_rate is not None:
            history_value["key_rate"] = str(result.key_rate)
        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=case.client_id,
            case_id=case.id,
            action="CALCULATION_COMPLETED",
            new_value=history_value,
        )
        await self.db.flush()
        return result