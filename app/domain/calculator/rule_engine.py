from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
)
from typing import Any


_SUPPORTED_ROUNDING = {
    "ROUND_HALF_UP": ROUND_HALF_UP,
    "ROUND_HALF_DOWN": ROUND_HALF_DOWN,
    "ROUND_HALF_EVEN": ROUND_HALF_EVEN,
    "ROUND_DOWN": ROUND_DOWN,
    "ROUND_UP": ROUND_UP,
    "ROUND_FLOOR": ROUND_FLOOR,
    "ROUND_CEILING": ROUND_CEILING,
}
_SUPPORTED_FORMULA = "price_rate_divisor_days_multiplier"


class CalculationRuleError(ValueError):
    """The approved rule bundle or calculation input is incomplete/ambiguous."""


@dataclass(frozen=True)
class RuleBasedCalculationInput:
    contract_price: Decimal
    planned_transfer_date: date
    calculation_date: date
    object_transferred: bool
    actual_transfer_date: date | None = None
    client_type: str = "consumer"


@dataclass(frozen=True)
class RuleBasedCalculationResult:
    contract_price: Decimal
    planned_transfer_date: date
    calculation_date: date
    object_transferred: bool
    actual_transfer_date: date | None
    delay_days: int
    delay_days_total: int
    delay_days_chargeable: int
    moratorium_days: int
    key_rate: Decimal | None
    consumer_multiplier: Decimal
    client_type: str
    penalty_amount: Decimal
    formula_version: str
    formula: str
    recommended_route: str
    rule_revision_id: int
    rule_revision_key: str
    rule_snapshot_sha256: str
    rule_snapshot: dict[str, Any]
    applied_segments: list[dict[str, Any]]
    is_preliminary: bool = True
    warning: str | None = None


@dataclass(frozen=True)
class _DateInterval:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def _decimal(value: object, title: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise CalculationRuleError(f"{title}: некорректное число") from error
    if not result.is_finite():
        raise CalculationRuleError(f"{title}: число должно быть конечным")
    return result


def _iso_date(value: object, title: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as error:
        raise CalculationRuleError(f"{title}: ожидается дата YYYY-MM-DD") from error


def _optional_iso_date(value: object, title: str) -> date | None:
    if value in (None, ""):
        return None
    return _iso_date(value, title)


def _clip(interval: _DateInterval, start: date, end: date) -> _DateInterval | None:
    clipped_start = max(interval.start, start)
    clipped_end = min(interval.end, end)
    if clipped_start > clipped_end:
        return None
    return _DateInterval(clipped_start, clipped_end)


def _merge(intervals: list[_DateInterval]) -> list[_DateInterval]:
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda item: (item.start, item.end))
    merged = [ordered[0]]
    for item in ordered[1:]:
        last = merged[-1]
        if item.start <= last.end + timedelta(days=1):
            merged[-1] = _DateInterval(last.start, max(last.end, item.end))
        else:
            merged.append(item)
    return merged


def _subtract(base: _DateInterval, excluded: list[_DateInterval]) -> list[_DateInterval]:
    result: list[_DateInterval] = []
    cursor = base.start
    for item in excluded:
        clipped = _clip(item, base.start, base.end)
        if clipped is None:
            continue
        if cursor < clipped.start:
            result.append(_DateInterval(cursor, clipped.start - timedelta(days=1)))
        cursor = max(cursor, clipped.end + timedelta(days=1))
        if cursor > base.end:
            break
    if cursor <= base.end:
        result.append(_DateInterval(cursor, base.end))
    return result


def _parse_rule_payload(rules: dict[str, Any], *, client_type: str) -> dict[str, Any]:
    if not isinstance(rules, dict):
        raise CalculationRuleError("Набор правил должен быть JSON-объектом")
    if rules.get("schema_version") != 1:
        raise CalculationRuleError("Неподдерживаемая версия схемы правил")
    if rules.get("formula_code") != _SUPPORTED_FORMULA:
        raise CalculationRuleError("Неподдерживаемая формула расчёта")

    try:
        start_offset = int(rules["delay_start_offset_days"])
    except (KeyError, TypeError, ValueError) as error:
        raise CalculationRuleError("Не задано правило начала периода просрочки") from error
    if start_offset < 0:
        raise CalculationRuleError("Смещение начала просрочки не может быть отрицательным")

    divisor = _decimal(rules.get("divisor"), "Делитель формулы")
    if divisor <= 0:
        raise CalculationRuleError("Делитель формулы должен быть положительным")

    client_types = rules.get("client_types")
    if not isinstance(client_types, dict) or client_type not in client_types:
        raise CalculationRuleError(
            f"Для типа клиента {client_type!r} нет утверждённого коэффициента"
        )
    client_rule = client_types[client_type]
    if not isinstance(client_rule, dict):
        raise CalculationRuleError("Правило типа клиента должно быть JSON-объектом")
    multiplier = _decimal(client_rule.get("multiplier"), "Коэффициент клиента")
    if multiplier <= 0:
        raise CalculationRuleError("Коэффициент клиента должен быть положительным")

    money_quant = _decimal(rules.get("money_quant"), "Шаг округления")
    if money_quant <= 0:
        raise CalculationRuleError("Шаг округления должен быть положительным")
    rounding_name = str(rules.get("rounding_mode") or "")
    if rounding_name not in _SUPPORTED_ROUNDING:
        raise CalculationRuleError("Не задан поддерживаемый режим округления")
    rounding_stage = str(rules.get("rounding_stage") or "")
    if rounding_stage not in {"total", "segment"}:
        raise CalculationRuleError("rounding_stage должен быть total или segment")

    raw_rates = rules.get("rates")
    if not isinstance(raw_rates, list) or not raw_rates:
        raise CalculationRuleError("В утверждённом наборе правил отсутствуют ставки")
    rates: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_rates):
        if not isinstance(raw, dict):
            raise CalculationRuleError(f"Ставка #{index + 1} должна быть объектом")
        start = _iso_date(raw.get("start"), f"Ставка #{index + 1}: start")
        end = _optional_iso_date(raw.get("end"), f"Ставка #{index + 1}: end")
        if end is not None and end < start:
            raise CalculationRuleError(f"Ставка #{index + 1}: end раньше start")
        rate = _decimal(raw.get("rate"), f"Ставка #{index + 1}")
        if rate < 0:
            raise CalculationRuleError(f"Ставка #{index + 1} не может быть отрицательной")
        rates.append(
            {
                "start": start,
                "end": end,
                "rate": rate,
                "code": str(raw.get("code") or f"rate-{index + 1}"),
            }
        )
    ordered_rates = sorted(rates, key=lambda item: item["start"])
    for previous, current in zip(ordered_rates, ordered_rates[1:]):
        previous_end = previous["end"] or date.max
        if current["start"] <= previous_end:
            raise CalculationRuleError("Периоды ставок пересекаются")

    exclusions: list[dict[str, Any]] = []
    raw_exclusions = rules.get("excluded_periods", [])
    if not isinstance(raw_exclusions, list):
        raise CalculationRuleError("excluded_periods должен быть списком")
    for index, raw in enumerate(raw_exclusions):
        if not isinstance(raw, dict):
            raise CalculationRuleError(
                f"Исключённый период #{index + 1} должен быть объектом"
            )
        start = _iso_date(raw.get("start"), f"Исключение #{index + 1}: start")
        end = _iso_date(raw.get("end"), f"Исключение #{index + 1}: end")
        if end < start:
            raise CalculationRuleError(
                f"Исключение #{index + 1}: end раньше start"
            )
        exclusions.append(
            {
                "start": start,
                "end": end,
                "kind": str(raw.get("kind") or "excluded"),
                "code": str(raw.get("code") or f"excluded-{index + 1}"),
            }
        )

    return {
        "start_offset": start_offset,
        "divisor": divisor,
        "multiplier": multiplier,
        "money_quant": money_quant,
        "rounding_name": rounding_name,
        "rounding": _SUPPORTED_ROUNDING[rounding_name],
        "rounding_stage": rounding_stage,
        "rates": ordered_rates,
        "exclusions": exclusions,
    }


class CalculationRuleEngine:
    """Apply one immutable approved rule revision without legal defaults."""

    def calculate(
        self,
        data: RuleBasedCalculationInput,
        *,
        rule_revision_id: int,
        rule_revision_key: str,
        rule_snapshot_sha256: str,
        rule_snapshot: dict[str, Any],
    ) -> RuleBasedCalculationResult:
        price = _decimal(data.contract_price, "Стоимость по ДДУ")
        if price <= 0:
            raise CalculationRuleError("Стоимость по ДДУ должна быть больше нуля")
        if data.calculation_date < data.planned_transfer_date:
            raise CalculationRuleError(
                "Дата расчёта не может быть раньше договорной даты передачи"
            )
        if data.object_transferred:
            if data.actual_transfer_date is None:
                raise CalculationRuleError(
                    "Для переданного объекта нужна фактическая дата передачи"
                )
            if data.actual_transfer_date > data.calculation_date:
                raise CalculationRuleError(
                    "Фактическая дата передачи не может быть позже даты расчёта"
                )
            end_date = data.actual_transfer_date
        else:
            if data.actual_transfer_date is not None:
                raise CalculationRuleError(
                    "Фактическая дата не передаётся, пока объект не передан"
                )
            end_date = data.calculation_date

        parsed = _parse_rule_payload(rule_snapshot, client_type=data.client_type)
        period_start = data.planned_transfer_date + timedelta(
            days=parsed["start_offset"]
        )
        if end_date < period_start:
            total_days = 0
            chargeable: list[_DateInterval] = []
            merged_excluded: list[_DateInterval] = []
        else:
            base = _DateInterval(period_start, end_date)
            total_days = base.days
            exclusion_intervals = [
                _DateInterval(item["start"], item["end"])
                for item in parsed["exclusions"]
            ]
            merged_excluded = _merge(
                [
                    clipped
                    for item in exclusion_intervals
                    if (clipped := _clip(item, base.start, base.end)) is not None
                ]
            )
            chargeable = _subtract(base, merged_excluded)

        moratorium_days = sum(item.days for item in merged_excluded)
        chargeable_days = sum(item.days for item in chargeable)
        applied_segments: list[dict[str, Any]] = []
        raw_total = Decimal("0")
        covered_days = 0
        unique_rates: set[Decimal] = set()

        for interval in chargeable:
            for rate_rule in parsed["rates"]:
                rate_end = rate_rule["end"] or date.max
                overlap = _clip(
                    interval,
                    rate_rule["start"],
                    rate_end,
                )
                if overlap is None:
                    continue
                days = overlap.days
                covered_days += days
                rate = rate_rule["rate"]
                unique_rates.add(rate)
                raw_amount = (
                    price
                    * rate
                    / parsed["divisor"]
                    * Decimal(days)
                    * parsed["multiplier"]
                )
                amount = raw_amount
                if parsed["rounding_stage"] == "segment":
                    amount = raw_amount.quantize(
                        parsed["money_quant"],
                        rounding=parsed["rounding"],
                    )
                raw_total += amount
                applied_segments.append(
                    {
                        "start": overlap.start.isoformat(),
                        "end": overlap.end.isoformat(),
                        "days": days,
                        "rate": str(rate),
                        "rate_code": rate_rule["code"],
                        "amount_before_final_rounding": str(amount),
                    }
                )

        if covered_days != chargeable_days:
            raise CalculationRuleError(
                "Утверждённые ставки не покрывают весь начисляемый период"
            )

        amount = raw_total.quantize(
            parsed["money_quant"],
            rounding=parsed["rounding"],
        )
        single_rate = next(iter(unique_rates)) if len(unique_rates) == 1 else None
        warning = None
        if total_days == 0:
            warning = "Просрочка на выбранную дату не обнаружена."
        elif chargeable_days == 0:
            warning = "Весь период просрочки исключён утверждёнными правилами расчёта."
        elif amount == 0:
            warning = "Расчёт по утверждённым правилам дал нулевую сумму."

        formula = (
            f"rule={rule_revision_key}; formula={_SUPPORTED_FORMULA}; "
            f"divisor={parsed['divisor']}; multiplier={parsed['multiplier']}; "
            f"chargeable_days={chargeable_days}; segments={len(applied_segments)}"
        )
        return RuleBasedCalculationResult(
            contract_price=price.quantize(
                parsed["money_quant"], rounding=parsed["rounding"]
            ),
            planned_transfer_date=data.planned_transfer_date,
            calculation_date=data.calculation_date,
            object_transferred=data.object_transferred,
            actual_transfer_date=data.actual_transfer_date,
            delay_days=chargeable_days,
            delay_days_total=total_days,
            delay_days_chargeable=chargeable_days,
            moratorium_days=moratorium_days,
            key_rate=single_rate,
            consumer_multiplier=parsed["multiplier"],
            client_type=data.client_type,
            penalty_amount=amount,
            formula_version=f"rule:{rule_revision_key}",
            formula=formula,
            recommended_route=(
                "M1" if chargeable_days > 0 and amount > 0 else "M2"
            ),
            rule_revision_id=int(rule_revision_id),
            rule_revision_key=str(rule_revision_key),
            rule_snapshot_sha256=str(rule_snapshot_sha256),
            rule_snapshot=rule_snapshot,
            applied_segments=applied_segments,
            warning=warning,
        )


__all__ = [
    "CalculationRuleEngine",
    "CalculationRuleError",
    "RuleBasedCalculationInput",
    "RuleBasedCalculationResult",
]
