from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from app.domain.calculator.calculation_rule_snapshot import (
    ExclusionPeriodSnapshot,
    RatePeriodSnapshot,
    ResolvedCalculationRule,
)
from app.domain.calculator.calculation_strategies import (
    DATE_STRATEGY_DAY_AFTER_PLANNED_TO_END_INCLUSIVE,
    calculate_raw_segment_amount,
    require_supported_date_strategy,
    round_segment_amount,
)
from app.domain.calculator.legal_rule_errors import (
    CalculationRuleAmbiguousError,
    CalculationRuleUnavailableError,
)
from app.domain.calculator.penalty_calculator import PenaltyCalculationError


@dataclass(frozen=True)
class SegmentedCalculationInput:
    contract_price: Decimal
    planned_transfer_date: date
    calculation_date: date
    object_transferred: bool
    actual_transfer_date: date | None = None


@dataclass(frozen=True)
class CalculationSegmentResult:
    sequence_no: int
    period_from: date
    period_to: date
    days_total: int
    days_excluded: int
    days_chargeable: int
    rate_period_id: int | None
    rate_value: Decimal
    consumer_multiplier: Decimal
    amount: Decimal
    exclusion_evidence: tuple[dict, ...]


@dataclass(frozen=True)
class SegmentedCalculationResult:
    contract_price: Decimal
    planned_transfer_date: date
    calculation_date: date
    calculation_end_date: date
    object_transferred: bool
    actual_transfer_date: date | None
    delay_days_total: int
    delay_days_chargeable: int
    moratorium_days: int
    penalty_amount: Decimal
    segments: tuple[CalculationSegmentResult, ...]
    formula_code: str
    rounding_code: str
    rule_set_id: int
    rule_revision: int
    recommended_route: str
    is_preliminary: bool = True
    warning: str | None = None


class CalculationPeriodService:
    """Pure deterministic segment builder/executor for one approved rule snapshot."""

    def calculate(
        self,
        *,
        data: SegmentedCalculationInput,
        rule: ResolvedCalculationRule,
    ) -> SegmentedCalculationResult:
        price = self._decimal(data.contract_price, "Стоимость по ДДУ")
        if price <= 0 or price > Decimal("1000000000000"):
            raise PenaltyCalculationError(
                "Стоимость по ДДУ должна быть больше нуля и не превышать 1 трлн рублей"
            )
        if data.calculation_date < data.planned_transfer_date:
            raise PenaltyCalculationError(
                "Дата расчёта не может быть раньше договорной даты передачи"
            )

        end_date = self._end_date(data)
        date_strategy = require_supported_date_strategy(rule.date_strategy_code)
        if date_strategy != DATE_STRATEGY_DAY_AFTER_PLANNED_TO_END_INCLUSIVE:
            raise CalculationRuleUnavailableError(
                "Утверждённая стратегия расчётных дат не реализована"
            )

        multiplier = rule.consumer_multiplier
        if multiplier is None:
            raise CalculationRuleUnavailableError(
                "Для формулы отсутствует утверждённый коэффициент типа клиента"
            )
        multiplier = self._decimal(multiplier, "Коэффициент типа клиента")
        if multiplier <= 0:
            raise CalculationRuleUnavailableError(
                "Коэффициент типа клиента должен быть больше нуля"
            )

        delay_days_total = max((end_date - data.planned_transfer_date).days, 0)
        if delay_days_total == 0:
            return SegmentedCalculationResult(
                contract_price=price,
                planned_transfer_date=data.planned_transfer_date,
                calculation_date=data.calculation_date,
                calculation_end_date=end_date,
                object_transferred=data.object_transferred,
                actual_transfer_date=data.actual_transfer_date,
                delay_days_total=0,
                delay_days_chargeable=0,
                moratorium_days=0,
                penalty_amount=Decimal("0.00"),
                segments=(),
                formula_code=rule.formula_code,
                rounding_code=rule.rounding_code,
                rule_set_id=rule.rule_set_id,
                rule_revision=rule.revision,
                recommended_route="M2",
                warning="Просрочка на выбранную дату не обнаружена.",
            )

        period_from = data.planned_transfer_date + timedelta(days=1)
        period_to = end_date
        boundaries = {period_from, period_to + timedelta(days=1)}
        self._add_boundaries(boundaries, period_from, period_to, rule.rates)
        self._add_boundaries(boundaries, period_from, period_to, rule.exclusions)
        ordered = sorted(boundaries)

        segments: list[CalculationSegmentResult] = []
        for index in range(len(ordered) - 1):
            segment_from = ordered[index]
            next_boundary = ordered[index + 1]
            if segment_from >= next_boundary:
                continue
            segment_to = next_boundary - timedelta(days=1)
            if segment_to < period_from or segment_from > period_to:
                continue
            segment_from = max(segment_from, period_from)
            segment_to = min(segment_to, period_to)
            days_total = (segment_to - segment_from).days + 1

            exclusions = self._applicable_exclusions(
                segment_from,
                rule.exclusions,
            )
            is_excluded = bool(exclusions)
            applicable_rates = self._applicable_rates(segment_from, rule.rates)

            if is_excluded:
                rate_period = applicable_rates[0] if len(applicable_rates) == 1 else None
                rate_value = (
                    Decimal(rate_period.rate_value)
                    if rate_period is not None
                    else Decimal("0")
                )
                days_excluded = days_total
                days_chargeable = 0
                amount = Decimal("0.00")
            else:
                if not applicable_rates:
                    raise CalculationRuleUnavailableError(
                        "В утверждённой редакции нет ставки для части периода просрочки"
                    )
                if len(applicable_rates) > 1:
                    raise CalculationRuleAmbiguousError(
                        "На один день просрочки одновременно действуют несколько ставок"
                    )
                rate_period = applicable_rates[0]
                rate_value = self._decimal(rate_period.rate_value, "Ставка")
                if rate_value < 0 or rate_value > Decimal("1"):
                    raise CalculationRuleUnavailableError(
                        "Ставка для выбранной формулы должна быть долей от 0 до 1"
                    )
                days_excluded = 0
                days_chargeable = days_total
                raw_amount = calculate_raw_segment_amount(
                    formula_code=rule.formula_code,
                    contract_price=price,
                    rate_value=rate_value,
                    chargeable_days=days_chargeable,
                    consumer_multiplier=multiplier,
                )
                amount = round_segment_amount(
                    rounding_code=rule.rounding_code,
                    value=raw_amount,
                )

            segments.append(
                CalculationSegmentResult(
                    sequence_no=len(segments) + 1,
                    period_from=segment_from,
                    period_to=segment_to,
                    days_total=days_total,
                    days_excluded=days_excluded,
                    days_chargeable=days_chargeable,
                    rate_period_id=(rate_period.id if rate_period is not None else None),
                    rate_value=rate_value,
                    consumer_multiplier=multiplier,
                    amount=amount,
                    exclusion_evidence=tuple(
                        {
                            "id": item.id,
                            "type": item.exclusion_type,
                            "date_from": item.date_from.isoformat(),
                            "date_to": item.date_to.isoformat(),
                            "source_reference": item.source_reference,
                        }
                        for item in exclusions
                    ),
                )
            )

        covered_days = sum(item.days_total for item in segments)
        if covered_days != delay_days_total:
            raise CalculationRuleUnavailableError(
                "Сегментация периода расчёта неполна; расчёт остановлен"
            )
        chargeable_days = sum(item.days_chargeable for item in segments)
        excluded_days = sum(item.days_excluded for item in segments)
        if chargeable_days + excluded_days != delay_days_total:
            raise CalculationRuleUnavailableError(
                "Контроль дней расчёта не сошёлся; расчёт остановлен"
            )

        penalty_amount = sum(
            (item.amount for item in segments),
            start=Decimal("0.00"),
        )
        warning = None
        if chargeable_days == 0:
            warning = "Весь период просрочки исключён из начисления утверждёнными правилами."
        elif penalty_amount == 0:
            warning = "Расчёт дал нулевую сумму; требуется проверка применённых правил."

        return SegmentedCalculationResult(
            contract_price=price,
            planned_transfer_date=data.planned_transfer_date,
            calculation_date=data.calculation_date,
            calculation_end_date=end_date,
            object_transferred=data.object_transferred,
            actual_transfer_date=data.actual_transfer_date,
            delay_days_total=delay_days_total,
            delay_days_chargeable=chargeable_days,
            moratorium_days=excluded_days,
            penalty_amount=penalty_amount,
            segments=tuple(segments),
            formula_code=rule.formula_code,
            rounding_code=rule.rounding_code,
            rule_set_id=rule.rule_set_id,
            rule_revision=rule.revision,
            recommended_route=(
                "M1" if chargeable_days > 0 and penalty_amount > 0 else "M2"
            ),
            warning=warning,
        )

    @staticmethod
    def _end_date(data: SegmentedCalculationInput) -> date:
        if data.object_transferred:
            if data.actual_transfer_date is None:
                raise PenaltyCalculationError(
                    "Для переданного объекта нужна фактическая дата передачи"
                )
            if data.actual_transfer_date > data.calculation_date:
                raise PenaltyCalculationError(
                    "Фактическая дата передачи не может быть позже даты расчёта"
                )
            return data.actual_transfer_date
        if data.actual_transfer_date is not None:
            raise PenaltyCalculationError(
                "Фактическая дата не передаётся, пока объект не передан"
            )
        return data.calculation_date

    @staticmethod
    def _decimal(value, title: str) -> Decimal:
        try:
            result = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise PenaltyCalculationError(f"{title}: некорректное число") from error
        if not result.is_finite():
            raise PenaltyCalculationError(f"{title}: число должно быть конечным")
        return result

    @staticmethod
    def _add_boundaries(
        boundaries: set[date],
        period_from: date,
        period_to: date,
        intervals,
    ) -> None:
        for item in intervals:
            raw_from = getattr(item, "valid_from", None) or getattr(item, "date_from")
            raw_to = getattr(item, "valid_to", None)
            if not hasattr(item, "valid_to"):
                raw_to = getattr(item, "date_to")
            interval_to = raw_to or period_to
            if interval_to < period_from or raw_from > period_to:
                continue
            intersection_from = max(period_from, raw_from)
            intersection_to = min(period_to, interval_to)
            boundaries.add(intersection_from)
            if intersection_to < period_to:
                boundaries.add(intersection_to + timedelta(days=1))

    @staticmethod
    def _applicable_rates(
        day: date,
        rates: tuple[RatePeriodSnapshot, ...],
    ) -> list[RatePeriodSnapshot]:
        return [
            item
            for item in rates
            if item.valid_from <= day and (item.valid_to is None or item.valid_to >= day)
        ]

    @staticmethod
    def _applicable_exclusions(
        day: date,
        exclusions: tuple[ExclusionPeriodSnapshot, ...],
    ) -> list[ExclusionPeriodSnapshot]:
        return [
            item
            for item in exclusions
            if item.date_from <= day <= item.date_to
        ]
