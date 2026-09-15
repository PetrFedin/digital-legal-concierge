from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.calculator.calculation_period_service import (
    CalculationPeriodService,
    SegmentedCalculationInput,
    SegmentedCalculationResult,
)
from app.domain.calculator.calculation_rule_snapshot import (
    ResolvedCalculationRule,
    canonical_rule_snapshot,
    canonical_rule_snapshot_hash,
    resolved_rule_from_orm,
)
from app.domain.calculator.legal_rule_service import LegalRuleService
from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.statuses.case_statuses import CaseStatus
from app.models.calculation import Calculation
from app.models.calculation_segment import CalculationSegment
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
    """Persist preliminary calculations as immutable, reproducible facts.

    PM-016 makes an APPROVED/effective-dated legal rule revision the production
    source of calculation truth. The service deliberately has no fallback to
    settings.legal_key_rate or to an implicit client multiplier. A synthetic
    ResolvedCalculationRule may be injected by focused tests, but normal runtime
    callers resolve the approved database revision.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.period_calculator = CalculationPeriodService()

    async def latest_calculation_for_case(self, *, case_id: int) -> Calculation | None:
        statement = (
            select(Calculation)
            .where(Calculation.case_id == int(case_id))
            .order_by(Calculation.created_at.desc(), Calculation.id.desc())
            .limit(1)
        )
        return (await self.db.execute(statement)).scalar_one_or_none()

    async def require_m1_eligible_calculation(self, *, case_id: int) -> Calculation:
        """Require the newest stored result to have chargeable delay and amount.

        New PM-016 calculations use delay_days_chargeable. Historical rows did
        not have that field, so they retain the previous delay_days eligibility
        semantics instead of being rewritten to pretend a newer rule revision
        existed when they were calculated.
        """

        calculation = await self.latest_calculation_for_case(case_id=case_id)
        if calculation is None:
            raise CalculatorRouteEligibilityError(
                "Для продолжения М1 нужен сохранённый предварительный расчёт."
            )
        chargeable_days = (
            int(calculation.delay_days_chargeable)
            if calculation.delay_days_chargeable is not None
            else int(calculation.delay_days or 0)
        )
        penalty_amount = Decimal(calculation.penalty_amount or 0)
        if chargeable_days <= 0 or penalty_amount <= 0:
            raise CalculatorRouteEligibilityError(
                "По последнему расчёту начисляемая просрочка или положительная сумма неустойки отсутствует."
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
        resolved_rule: ResolvedCalculationRule | None = None,
    ) -> SegmentedCalculationResult:
        """Calculate once from an exact rule snapshot and append immutable evidence.

        The caller owns commit/rollback. If no approved rule applies, resolution
        fails closed before any Calculation row is inserted.
        """

        rule = resolved_rule
        if rule is None:
            orm_rule = await LegalRuleService(self.db).resolve_rule_set(
                as_of=calculation_date
            )
            rule = resolved_rule_from_orm(orm_rule)

        result = self.period_calculator.calculate(
            data=SegmentedCalculationInput(
                contract_price=contract_price,
                planned_transfer_date=planned_transfer_date,
                calculation_date=calculation_date,
                object_transferred=object_transferred,
                actual_transfer_date=actual_transfer_date,
            ),
            rule=rule,
        )
        snapshot = canonical_rule_snapshot(rule)
        snapshot_hash = canonical_rule_snapshot_hash(rule)

        # The old scalar key_rate column is retained only as a compatibility
        # projection when one chargeable rate really did apply. A multi-rate
        # calculation never fabricates a single rate; segments + snapshot are
        # the authoritative evidence.
        chargeable_rates = {
            Decimal(segment.rate_value)
            for segment in result.segments
            if segment.days_chargeable > 0
        }
        compatibility_rate = (
            next(iter(chargeable_rates)) if len(chargeable_rates) == 1 else None
        )
        compatibility_formula_version = f"ruleset:{rule.rule_set_id}:r{rule.revision}"

        calculation = Calculation(
            case_id=case.id,
            contract_price=result.contract_price,
            planned_transfer_date=result.planned_transfer_date,
            calculation_date=result.calculation_date,
            actual_transfer_date=result.actual_transfer_date,
            object_transferred=result.object_transferred,
            # Legacy projection: gross delay. M1 eligibility for new rows uses
            # delay_days_chargeable explicitly.
            delay_days=result.delay_days_total,
            key_rate=compatibility_rate,
            consumer_multiplier=rule.consumer_multiplier,
            penalty_amount=result.penalty_amount,
            formula_version=compatibility_formula_version,
            is_preliminary=True,
            rule_set_id=rule.rule_set_id,
            rule_revision=rule.revision,
            rule_snapshot_hash=snapshot_hash,
            rule_snapshot_json=snapshot,
            calculation_end_date=result.calculation_end_date,
            delay_days_total=result.delay_days_total,
            delay_days_chargeable=result.delay_days_chargeable,
            moratorium_days=result.moratorium_days,
        )
        self.db.add(calculation)
        await self.db.flush()

        for segment in result.segments:
            self.db.add(
                CalculationSegment(
                    calculation_id=calculation.id,
                    rule_set_id=rule.rule_set_id,
                    rule_revision=rule.revision,
                    sequence_no=segment.sequence_no,
                    period_from=segment.period_from,
                    period_to=segment.period_to,
                    days_total=segment.days_total,
                    days_excluded=segment.days_excluded,
                    days_chargeable=segment.days_chargeable,
                    rate_period_id=segment.rate_period_id,
                    rate_value=segment.rate_value,
                    consumer_multiplier=segment.consumer_multiplier,
                    amount=segment.amount,
                    exclusion_evidence=list(segment.exclusion_evidence) or None,
                )
            )

        # Recalculation must not silently move a Case backwards from an active
        # legal or consultation stage. Only the calculator phase is advanced.
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
                "delay_days_total": result.delay_days_total,
                "delay_days_chargeable": result.delay_days_chargeable,
                "moratorium_days": result.moratorium_days,
                "calculation_date": result.calculation_date.isoformat(),
                "calculation_end_date": result.calculation_end_date.isoformat(),
                "rule_set_id": rule.rule_set_id,
                "rule_revision": rule.revision,
                "rule_snapshot_hash": snapshot_hash,
                "formula_code": rule.formula_code,
                "rounding_code": rule.rounding_code,
                "segment_count": len(result.segments),
            },
        )
        await self.db.flush()
        return result
