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
_SUPPORTED_FORMULA_V1 = "price_rate_divisor_days_multiplier"
_SUPPORTED_FORMULA_V2 = "ddu_214fz_art6_due_date_rate_v2"


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
    unique_object: bool | None = None
    acceptance_evasion: str | None = None
    deadline_confirmed: bool | None = None
    ddu_signing_date: date | None = None


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
    base_rate_date: date | None = None
    base_rate: Decimal | None = None
    calculation_branch: str = "standard"
    penalty_cap_applied: bool = False
    applied_source_ids: tuple[str, ...] = ()


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


def _source_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        str(item).strip()
        for item in value
        if str(item or "").strip()
    )


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


def _add_months(value: date, months: int) -> date:
    total = value.year * 12 + (value.month - 1) + int(months)
    year, month0 = divmod(total, 12)
    month = month0 + 1
    month_lengths = (
        31,
        29 if year % 400 == 0 or (year % 4 == 0 and year % 100 != 0) else 28,
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    )
    return date(year, month, min(value.day, month_lengths[month - 1]))


def _parse_rule_payload_v1(rules: dict[str, Any], *, client_type: str) -> dict[str, Any]:
    if rules.get("formula_code") != _SUPPORTED_FORMULA_V1:
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
        "schema_version": 1,
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


def _parse_rate_schedule(raw_rates: object) -> list[dict[str, Any]]:
    if not isinstance(raw_rates, list) or not raw_rates:
        raise CalculationRuleError("Справочник ставок ЦБ пуст")
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_rates):
        if not isinstance(raw, dict):
            raise CalculationRuleError(f"Ставка ЦБ #{index + 1} должна быть объектом")
        start = _iso_date(raw.get("start"), f"Ставка ЦБ #{index + 1}: start")
        rate = _decimal(raw.get("rate"), f"Ставка ЦБ #{index + 1}")
        if rate < 0:
            raise CalculationRuleError(f"Ставка ЦБ #{index + 1} не может быть отрицательной")
        rows.append(
            {
                "start": start,
                "rate": rate,
                "code": str(raw.get("code") or f"CBR-{start.isoformat()}"),
                "source_ids": _source_ids(raw.get("source_ids")),
            }
        )
    ordered = sorted(rows, key=lambda item: item["start"])
    for previous, current in zip(ordered, ordered[1:]):
        if previous["start"] == current["start"]:
            raise CalculationRuleError("Справочник ставок ЦБ содержит дублирующиеся даты")
    return ordered


def _parse_periods(raw_items: object, *, title: str, cap: bool = False) -> list[dict[str, Any]]:
    if raw_items in (None, ""):
        return []
    if not isinstance(raw_items, list):
        raise CalculationRuleError(f"{title} должны быть списком")
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise CalculationRuleError(f"{title} #{index + 1}: ожидается объект")
        start = _iso_date(raw.get("start"), f"{title} #{index + 1}: start")
        end = _iso_date(raw.get("end"), f"{title} #{index + 1}: end")
        if end < start:
            raise CalculationRuleError(f"{title} #{index + 1}: end раньше start")
        row = {
            "start": start,
            "end": end,
            "code": str(raw.get("code") or f"period-{index + 1}"),
            "source_ids": _source_ids(raw.get("source_ids")),
        }
        if cap:
            rate = _decimal(raw.get("cap_rate"), f"{title} #{index + 1}: cap_rate")
            if rate < 0:
                raise CalculationRuleError(f"{title} #{index + 1}: cap_rate отрицательна")
            row["cap_rate"] = rate
        rows.append(row)
    return sorted(rows, key=lambda item: (item["start"], item["end"]))


def _client_rule_v2(rules: dict[str, Any], *, client_type: str) -> tuple[str, dict[str, Any]]:
    standard = rules.get("standard_object")
    if not isinstance(standard, dict):
        raise CalculationRuleError("Не задана стандартная ветка расчёта")
    client_types = standard.get("participant_types")
    if not isinstance(client_types, dict):
        raise CalculationRuleError("Не заданы типы участников")

    aliases = {
        "consumer": "consumer_individual",
        "individual": "consumer_individual",
        "business": "other",
    }
    normalized = aliases.get(str(client_type), str(client_type))
    raw = client_types.get(normalized)
    if not isinstance(raw, dict):
        raise CalculationRuleError(
            f"Для типа клиента {client_type!r} нет утверждённого коэффициента"
        )
    return normalized, raw


def _parse_rule_payload_v2(rules: dict[str, Any], *, client_type: str) -> dict[str, Any]:
    if rules.get("formula_code") != _SUPPORTED_FORMULA_V2:
        raise CalculationRuleError("Неподдерживаемая формула v2")

    standard = rules.get("standard_object")
    if not isinstance(standard, dict):
        raise CalculationRuleError("Не задана стандартная ветка расчёта")

    period = rules.get("period")
    if not isinstance(period, dict) or period.get("start") != "day_after_contractual_due_date":
        raise CalculationRuleError("Не подтверждено правило начала просрочки")
    if period.get("end_if_transferred") != "transfer_document_date_inclusive":
        raise CalculationRuleError("Не подтверждено правило окончания просрочки")
    if period.get("end_if_not_transferred") != "calculation_date_inclusive":
        raise CalculationRuleError("Не подтверждено правило расчёта для непереданного объекта")

    divisor = _decimal(standard.get("divisor"), "Делитель формулы")
    if divisor <= 0:
        raise CalculationRuleError("Делитель формулы должен быть положительным")

    normalized_client_type, client_rule = _client_rule_v2(
        rules,
        client_type=client_type,
    )
    multiplier = _decimal(client_rule.get("multiplier"), "Коэффициент клиента")
    if multiplier <= 0:
        raise CalculationRuleError("Коэффициент клиента должен быть положительным")

    rounding = rules.get("rounding")
    if not isinstance(rounding, dict):
        raise CalculationRuleError("Не заданы правила округления")
    money_quant = _decimal(rounding.get("money_quant"), "Шаг округления")
    if money_quant <= 0:
        raise CalculationRuleError("Шаг округления должен быть положительным")
    rounding_name = str(rounding.get("mode") or "")
    if rounding_name not in _SUPPORTED_ROUNDING:
        raise CalculationRuleError("Не задан поддерживаемый режим округления")
    if str(rounding.get("stage") or "") != "final_total":
        raise CalculationRuleError("v2 поддерживает округление только итоговой суммы")

    rate_basis = standard.get("base_rate")
    if not isinstance(rate_basis, dict) or rate_basis.get("basis") != "rate_on_contractual_due_date":
        raise CalculationRuleError("Ставка должна определяться на договорную дату исполнения")

    rate_schedule = _parse_rate_schedule(standard.get("rate_schedule"))
    exclusions = _parse_periods(
        standard.get("excluded_periods"),
        title="Исключённые периоды",
    )
    caps = _parse_periods(
        standard.get("rate_cap_periods"),
        title="Периоды ограничения ставки",
        cap=True,
    )

    unique = rules.get("unique_object")
    if not isinstance(unique, dict):
        raise CalculationRuleError("Не задана отдельная ветка уникального объекта")

    judicial_source_ids: set[str] = set()
    for item in list(rules.get("judicial_adjustments") or []):
        if isinstance(item, dict):
            judicial_source_ids.update(_source_ids(item.get("source_ids")))

    return {
        "schema_version": 2,
        "divisor": divisor,
        "client_type": normalized_client_type,
        "multiplier": multiplier,
        "client_source_ids": _source_ids(client_rule.get("source_ids")),
        "money_quant": money_quant,
        "rounding_name": rounding_name,
        "rounding": _SUPPORTED_ROUNDING[rounding_name],
        "rounding_source_ids": _source_ids(rounding.get("source_ids")),
        "rate_schedule": rate_schedule,
        "rate_source_ids": _source_ids(rate_basis.get("source_ids")),
        "exclusions": exclusions,
        "caps": caps,
        "standard_source_ids": _source_ids(standard.get("source_ids")),
        "period_source_ids": _source_ids(period.get("source_ids")),
        "judicial_source_ids": tuple(sorted(judicial_source_ids)),
        "unique": unique,
    }


def _parse_rule_payload(rules: dict[str, Any], *, client_type: str) -> dict[str, Any]:
    if not isinstance(rules, dict):
        raise CalculationRuleError("Набор правил должен быть JSON-объектом")
    version = rules.get("schema_version")
    if version == 1:
        return _parse_rule_payload_v1(rules, client_type=client_type)
    if version == 2:
        return _parse_rule_payload_v2(rules, client_type=client_type)
    raise CalculationRuleError("Неподдерживаемая версия схемы правил")


def _resolve_due_date_rate(schedule: list[dict[str, Any]], due_date: date) -> dict[str, Any]:
    candidates = [row for row in schedule if row["start"] <= due_date]
    if not candidates:
        raise CalculationRuleError(
            "Справочник ставок ЦБ не покрывает договорную дату исполнения"
        )
    return max(candidates, key=lambda row: row["start"])


def _effective_rate_segments(
    interval: _DateInterval,
    *,
    base_rate: Decimal,
    base_code: str,
    base_source_ids: tuple[str, ...],
    cap_periods: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    boundaries = {interval.start, interval.end + timedelta(days=1)}
    for cap in cap_periods:
        clipped = _clip(_DateInterval(cap["start"], cap["end"]), interval.start, interval.end)
        if clipped is None:
            continue
        boundaries.add(clipped.start)
        boundaries.add(clipped.end + timedelta(days=1))
    ordered = sorted(boundaries)
    result: list[dict[str, Any]] = []
    for left, right_exclusive in zip(ordered, ordered[1:]):
        segment = _DateInterval(left, right_exclusive - timedelta(days=1))
        matching_caps = [
            cap
            for cap in cap_periods
            if _clip(_DateInterval(cap["start"], cap["end"]), segment.start, segment.end)
            is not None
        ]
        effective_rate = base_rate
        cap_code = None
        source_ids = set(base_source_ids)
        if matching_caps:
            strictest = min(matching_caps, key=lambda item: item["cap_rate"])
            if strictest["cap_rate"] < effective_rate:
                effective_rate = strictest["cap_rate"]
                cap_code = strictest["code"]
            source_ids.update(strictest["source_ids"])
        result.append(
            {
                "interval": segment,
                "rate": effective_rate,
                "base_rate": base_rate,
                "rate_code": base_code,
                "cap_code": cap_code,
                "source_ids": tuple(sorted(source_ids)),
            }
        )
    return result


def _calculate_v1(
    data: RuleBasedCalculationInput,
    parsed: dict[str, Any],
    *,
    rule_revision_id: int,
    rule_revision_key: str,
    rule_snapshot_sha256: str,
    rule_snapshot: dict[str, Any],
    price: Decimal,
    end_date: date,
) -> RuleBasedCalculationResult:
    period_start = data.planned_transfer_date + timedelta(days=parsed["start_offset"])
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
            overlap = _clip(interval, rate_rule["start"], rate_end)
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

    amount = raw_total.quantize(parsed["money_quant"], rounding=parsed["rounding"])
    single_rate = next(iter(unique_rates)) if len(unique_rates) == 1 else None
    warning = None
    if total_days == 0:
        warning = "Просрочка на выбранную дату не обнаружена."
    elif chargeable_days == 0:
        warning = "Весь период просрочки исключён утверждёнными правилами расчёта."
    elif amount == 0:
        warning = "Расчёт по утверждённым правилам дал нулевую сумму."

    formula = (
        f"rule={rule_revision_key}; formula={_SUPPORTED_FORMULA_V1}; "
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
        recommended_route="M1" if chargeable_days > 0 and amount > 0 else "M2",
        rule_revision_id=int(rule_revision_id),
        rule_revision_key=str(rule_revision_key),
        rule_snapshot_sha256=str(rule_snapshot_sha256),
        rule_snapshot=rule_snapshot,
        applied_segments=applied_segments,
        warning=warning,
    )


def _calculate_v2(
    data: RuleBasedCalculationInput,
    parsed: dict[str, Any],
    *,
    rule_revision_id: int,
    rule_revision_key: str,
    rule_snapshot_sha256: str,
    rule_snapshot: dict[str, Any],
    price: Decimal,
    end_date: date,
) -> RuleBasedCalculationResult:
    if data.deadline_confirmed is not True:
        raise CalculationRuleError(
            "Нужна проверка последнего действующего срока передачи по ДДУ и допсоглашениям"
        )
    evasion = str(data.acceptance_evasion or "").strip().lower()
    if evasion != "no":
        raise CalculationRuleError(
            "Нужно проверить обстоятельства приёмки: уклонение/уведомление нельзя определять автоматически"
        )
    if data.unique_object is None:
        raise CalculationRuleError(
            "Нужно определить, относится ли объект к уникальным по проектной документации"
        )

    branch = "unique" if data.unique_object else "standard"
    period_start = data.planned_transfer_date + timedelta(days=1)
    if end_date < period_start:
        total_days = 0
        base = None
    else:
        base = _DateInterval(period_start, end_date)
        total_days = base.days

    base_rate_rule = _resolve_due_date_rate(
        parsed["rate_schedule"],
        data.planned_transfer_date,
    )
    base_rate = base_rate_rule["rate"]
    base_rate_sources = tuple(
        sorted(
            set(parsed["rate_source_ids"])
            | set(base_rate_rule["source_ids"])
        )
    )

    if branch == "unique":
        unique = parsed["unique"]
        cutoff = _iso_date(
            unique.get("ddu_signed_before"),
            "Уникальный объект: граничная дата ДДУ",
        )
        if data.ddu_signing_date is None or data.ddu_signing_date >= cutoff:
            raise CalculationRuleError(
                "Для уникального объекта нужна проверка даты заключения ДДУ и применимости ч. 2.1 ст. 6"
            )
        max_months = int(unique.get("maximum_delay_months") or 0)
        if max_months <= 0:
            raise CalculationRuleError("Не задан предел длительности для уникального объекта")
        if end_date > _add_months(data.planned_transfer_date, max_months):
            raise CalculationRuleError(
                "Просрочка уникального объекта превышает автоматизируемый предел; нужна проверка юриста"
            )
        divisor = _decimal(unique.get("divisor"), "Делитель уникального объекта")
        multiplier = _decimal(unique.get("multiplier"), "Коэффициент уникального объекта")
        exclusions = _parse_periods(
            unique.get("excluded_periods"),
            title="Исключённые периоды уникального объекта",
        )
        caps: list[dict[str, Any]] = []
        branch_source_ids = _source_ids(unique.get("source_ids"))
        penalty_share_cap = _decimal(
            unique.get("maximum_penalty_share_of_contract_price"),
            "Лимит неустойки уникального объекта",
        )
    else:
        divisor = parsed["divisor"]
        multiplier = parsed["multiplier"]
        exclusions = parsed["exclusions"]
        caps = parsed["caps"]
        branch_source_ids = tuple(
            sorted(
                set(parsed["standard_source_ids"])
                | set(parsed["client_source_ids"])
                | set(parsed["period_source_ids"])
            )
        )
        penalty_share_cap = None

    if divisor <= 0 or multiplier <= 0:
        raise CalculationRuleError("Формула содержит недопустимый коэффициент")

    if base is None:
        merged_excluded: list[_DateInterval] = []
        chargeable: list[_DateInterval] = []
    else:
        exclusion_intervals = [
            _DateInterval(item["start"], item["end"])
            for item in exclusions
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
    applied_source_ids = (
        set(base_rate_sources)
        | set(branch_source_ids)
        | set(parsed["rounding_source_ids"])
        | set(parsed["judicial_source_ids"])
    )
    raw_total = Decimal("0")

    for interval in chargeable:
        segments = _effective_rate_segments(
            interval,
            base_rate=base_rate,
            base_code=base_rate_rule["code"],
            base_source_ids=base_rate_sources,
            cap_periods=caps,
        )
        for segment in segments:
            item = segment["interval"]
            days = item.days
            rate = segment["rate"]
            amount = price * rate / divisor * Decimal(days) * multiplier
            raw_total += amount
            applied_source_ids.update(segment["source_ids"])
            applied_segments.append(
                {
                    "start": item.start.isoformat(),
                    "end": item.end.isoformat(),
                    "days": days,
                    "base_rate_date": data.planned_transfer_date.isoformat(),
                    "base_rate": str(base_rate),
                    "effective_rate": str(rate),
                    "rate_code": segment["rate_code"],
                    "cap_code": segment["cap_code"],
                    "multiplier": str(multiplier),
                    "divisor": str(divisor),
                    "source_ids": list(segment["source_ids"]),
                    "amount_before_final_rounding": str(amount),
                }
            )

    for exclusion in exclusions:
        if base is None:
            continue
        if _clip(_DateInterval(exclusion["start"], exclusion["end"]), base.start, base.end):
            applied_source_ids.update(exclusion["source_ids"])

    amount = raw_total.quantize(
        parsed["money_quant"],
        rounding=parsed["rounding"],
    )
    penalty_cap_applied = False
    if penalty_share_cap is not None:
        maximum = (price * penalty_share_cap).quantize(
            parsed["money_quant"],
            rounding=parsed["rounding"],
        )
        if amount > maximum:
            amount = maximum
            penalty_cap_applied = True

    warning = None
    if total_days == 0:
        warning = "Просрочка на выбранную дату не обнаружена."
    elif chargeable_days == 0:
        warning = "Весь период просрочки исключён утверждёнными правилами расчёта."
    elif branch == "unique":
        warning = (
            "Расчёт для уникального объекта предварительный и требует проверки "
            "проектной документации и применимости ч. 2.1 ст. 6 юристом."
        )

    formula = (
        f"rule={rule_revision_key}; formula={_SUPPORTED_FORMULA_V2}; "
        f"branch={branch}; rate_date={data.planned_transfer_date.isoformat()}; "
        f"base_rate={base_rate}; divisor={divisor}; multiplier={multiplier}; "
        f"chargeable_days={chargeable_days}; segments={len(applied_segments)}"
    )
    recommended_route = (
        "M1"
        if branch == "standard" and chargeable_days > 0 and amount > 0
        else "M2"
    )

    return RuleBasedCalculationResult(
        contract_price=price.quantize(
            parsed["money_quant"],
            rounding=parsed["rounding"],
        ),
        planned_transfer_date=data.planned_transfer_date,
        calculation_date=data.calculation_date,
        object_transferred=data.object_transferred,
        actual_transfer_date=data.actual_transfer_date,
        delay_days=chargeable_days,
        delay_days_total=total_days,
        delay_days_chargeable=chargeable_days,
        moratorium_days=moratorium_days,
        key_rate=base_rate,
        consumer_multiplier=multiplier,
        client_type=parsed["client_type"],
        penalty_amount=amount,
        formula_version=f"rule:{rule_revision_key}",
        formula=formula,
        recommended_route=recommended_route,
        rule_revision_id=int(rule_revision_id),
        rule_revision_key=str(rule_revision_key),
        rule_snapshot_sha256=str(rule_snapshot_sha256),
        rule_snapshot=rule_snapshot,
        applied_segments=applied_segments,
        warning=warning,
        base_rate_date=data.planned_transfer_date,
        base_rate=base_rate,
        calculation_branch=branch,
        penalty_cap_applied=penalty_cap_applied,
        applied_source_ids=tuple(sorted(applied_source_ids)),
    )


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
        if parsed["schema_version"] == 1:
            return _calculate_v1(
                data,
                parsed,
                rule_revision_id=rule_revision_id,
                rule_revision_key=rule_revision_key,
                rule_snapshot_sha256=rule_snapshot_sha256,
                rule_snapshot=rule_snapshot,
                price=price,
                end_date=end_date,
            )
        return _calculate_v2(
            data,
            parsed,
            rule_revision_id=rule_revision_id,
            rule_revision_key=rule_revision_key,
            rule_snapshot_sha256=rule_snapshot_sha256,
            rule_snapshot=rule_snapshot,
            price=price,
            end_date=end_date,
        )


__all__ = [
    "CalculationRuleEngine",
    "CalculationRuleError",
    "RuleBasedCalculationInput",
    "RuleBasedCalculationResult",
    "_parse_rule_payload",
]
