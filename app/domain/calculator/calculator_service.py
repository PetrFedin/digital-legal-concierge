from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.calculator.intake_service import CalculationIntakeService
from app.domain.calculator.rule_engine import (
    CalculationRuleEngine,
    RuleBasedCalculationInput,
    RuleBasedCalculationResult,
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


def _result_from_persisted(calculation: Calculation) -> RuleBasedCalculationResult:
    """Reconstruct the already-committed result for an idempotent Telegram retry."""

    if (
        calculation.contract_price is None
        or calculation.planned_transfer_date is None
        or calculation.calculation_date is None
        or calculation.object_transferred is None
        or calculation.penalty_amount is None
        or calculation.consumer_multiplier is None
        or calculation.rule_revision_id is None
        or not calculation.rule_revision_key
        or not calculation.rule_snapshot_sha256
        or not isinstance(calculation.rule_snapshot, dict)
    ):
        raise CalculatorRouteEligibilityError(
            "Сохранённый расчёт не содержит полного набора воспроизводимых данных"
        )

    total_days = int(
        calculation.delay_days_total
        if calculation.delay_days_total is not None
        else calculation.delay_days or 0
    )
    chargeable_days = int(
        calculation.delay_days_chargeable
        if calculation.delay_days_chargeable is not None
        else calculation.delay_days or 0
    )
    excluded_days = int(calculation.moratorium_days or 0)
    amount = Decimal(calculation.penalty_amount)
    warning = None
    if total_days == 0:
        warning = "Просрочка на выбранную дату не обнаружена."
    elif chargeable_days == 0:
        warning = "Весь период просрочки исключён утверждёнными правилами расчёта."
    elif amount == 0:
        warning = "Расчёт по утверждённым правилам дал нулевую сумму."

    return RuleBasedCalculationResult(
        contract_price=Decimal(calculation.contract_price),
        planned_transfer_date=calculation.planned_transfer_date,
        calculation_date=calculation.calculation_date,
        object_transferred=bool(calculation.object_transferred),
        actual_transfer_date=calculation.actual_transfer_date,
        delay_days=chargeable_days,
        delay_days_total=total_days,
        delay_days_chargeable=chargeable_days,
        moratorium_days=excluded_days,
        key_rate=(
            Decimal(calculation.key_rate)
            if calculation.key_rate is not None
            else None
        ),
        consumer_multiplier=Decimal(calculation.consumer_multiplier),
        client_type=str(calculation.client_type or "consumer"),
        penalty_amount=amount,
        formula_version=str(calculation.formula_version or "rule:persisted"),
        formula=str(calculation.formula_version or "rule:persisted"),
        recommended_route=(
            "M1" if chargeable_days > 0 and amount > 0 else "M2"
        ),
        rule_revision_id=int(calculation.rule_revision_id),
        rule_revision_key=str(calculation.rule_revision_key),
        rule_snapshot_sha256=str(calculation.rule_snapshot_sha256),
        rule_snapshot=dict(calculation.rule_snapshot),
        applied_segments=list(calculation.applied_segments or []),
        unique_object=bool(getattr(calculation, "unique_object", False)),
        gross_penalty_amount=(
            Decimal(calculation.gross_penalty_amount)
            if getattr(calculation, "gross_penalty_amount", None) is not None
            else amount
        ),
        amount_cap=(
            Decimal(calculation.amount_cap)
            if getattr(calculation, "amount_cap", None) is not None
            else None
        ),
        amount_cap_applied=bool(getattr(calculation, "amount_cap_applied", False)),
        manual_review_required=bool(
            getattr(calculation, "manual_review_required", False)
        ),
        manual_review_reasons=list(
            getattr(calculation, "manual_review_reasons", None) or []
        ),
        excluded_segments=list(getattr(calculation, "excluded_segments", None) or []),
        is_preliminary=bool(calculation.is_preliminary),
        warning=warning,
    )


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

    async def result_for_case(self, *, case_id: int) -> RuleBasedCalculationResult | None:
        """Return the latest immutable result in presentation-safe domain form."""

        calculation = await self.latest_calculation_for_case(case_id=case_id)
        if calculation is None:
            return None
        return _result_from_persisted(calculation)

    async def require_m1_eligible_calculation(self, *, case_id: int) -> Calculation:
        """Require the current Case outcome to contain a positive charged delay/amount."""

        calculation = await self.latest_calculation_for_case(case_id=case_id)
        if calculation is None:
            raise CalculatorRouteEligibilityError(
                "Для продолжения М1 нужен сохранённый предварительный расчёт."
            )
        # Historical Calculation rows and older focused tests only have
        # delay_days. New PM-016 rows own delay_days_chargeable explicitly; do
        # not force legacy facts to masquerade as a newer schema revision.
        chargeable = getattr(calculation, "delay_days_chargeable", None)
        delay_days = int(
            chargeable if chargeable is not None else getattr(calculation, "delay_days", 0) or 0
        )
        penalty_amount = Decimal(getattr(calculation, "penalty_amount", 0) or 0)
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
        unique_object: bool = False,
        manual_review_flags: tuple[str, ...] = (),
        expected_rule_revision_key: str | None = None,
        expected_rule_snapshot_sha256: str | None = None,
    ) -> RuleBasedCalculationResult:
        """Create one immutable Calculation or return the already completed one.

        The current Case intake is row-locked for the whole finalization. If a
        previous attempt committed but Telegram failed before presentation, its
        ``completed_calculation_id`` is returned verbatim instead of appending a
        duplicate Calculation/history event. Explicit recalculation first resets
        that intake identity.
        """

        intake = await self.intakes.get_for_update(case_id=int(case.id))
        if intake is not None and intake.completed_calculation_id is not None:
            existing = await self.db.get(
                Calculation,
                int(intake.completed_calculation_id),
            )
            if existing is None or int(existing.case_id) != int(case.id):
                raise CalculatorRouteEligibilityError(
                    "Завершённый черновик ссылается на недоступный расчёт"
                )
            return _result_from_persisted(existing)

        # There is deliberately no settings/env/key-rate fallback. If the
        # lawyer-approved rule directory is absent, overlapping or corrupted,
        # calculation fails closed before any historical result is appended.
        revision = await self.rule_revisions.resolve(calculation_date=calculation_date)
        if (
            expected_rule_revision_key is not None
            and str(revision.revision_key) != str(expected_rule_revision_key)
        ):
            raise CalculatorRouteEligibilityError(
                "Версия юридических правил изменилась после предварительного просмотра. "
                "Повторите расчёт перед сохранением."
            )
        if (
            expected_rule_snapshot_sha256 is not None
            and str(revision.rules_sha256) != str(expected_rule_snapshot_sha256)
        ):
            raise CalculatorRouteEligibilityError(
                "Содержимое юридических правил изменилось после предварительного просмотра. "
                "Повторите расчёт перед сохранением."
            )
        result = self.calculator.calculate(
            RuleBasedCalculationInput(
                contract_price=contract_price,
                planned_transfer_date=planned_transfer_date,
                calculation_date=calculation_date,
                object_transferred=object_transferred,
                actual_transfer_date=actual_transfer_date,
                client_type=client_type,
                unique_object=bool(unique_object),
                manual_review_flags=tuple(manual_review_flags),
            ),
            rule_revision_id=int(revision.id),
            rule_revision_key=str(revision.revision_key),
            rule_snapshot_sha256=str(revision.rules_sha256),
            rule_snapshot=dict(revision.rules),
        )

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
            unique_object=result.unique_object,
            penalty_amount=result.penalty_amount,
            gross_penalty_amount=result.gross_penalty_amount,
            amount_cap=result.amount_cap,
            amount_cap_applied=result.amount_cap_applied,
            manual_review_required=result.manual_review_required,
            manual_review_reasons=result.manual_review_reasons,
            formula_version=result.formula_version,
            rule_revision_id=result.rule_revision_id,
            rule_revision_key=result.rule_revision_key,
            rule_snapshot_sha256=result.rule_snapshot_sha256,
            rule_snapshot=result.rule_snapshot,
            applied_segments=result.applied_segments,
            excluded_segments=result.excluded_segments,
            is_preliminary=True,
        )
        self.db.add(calculation)
        # The id must exist before it is sealed into the durable intake.
        await self.db.flush()

        await self.intakes.complete_from_result(
            case_id=int(case.id),
            result=result,
            calculation_id=int(calculation.id),
        )

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
            "unique_object": result.unique_object,
            "formula_version": result.formula_version,
            "rule_revision_id": result.rule_revision_id,
            "rule_revision_key": result.rule_revision_key,
            "rule_snapshot_sha256": result.rule_snapshot_sha256,
            "amount_cap_applied": result.amount_cap_applied,
            "manual_review_required": result.manual_review_required,
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
