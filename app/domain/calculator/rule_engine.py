from __future__ import annotations

import calendar
from dataclasses import dataclass, field
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
_SUPPORTED_FORMULA_V2 = "ddu_delay_penalty_v2"


class CalculationRuleError(ValueError):
    """The legal rule bundle or calculation input is incomplete/ambiguous."""


class CalculationManualReviewRequired(CalculationRuleError):
    """A configured legal stop-factor requires lawyer review before an amount."""

    def __init__(self, reasons: list[str]):
        self.reasons = [str(item) for item in reasons if str(item).strip()]
        super().__init__(
            "Требуется ручная юридическая проверка"
            + (": " + "; ".join(self.reasons) if self.reasons else "")
        )


@dataclass(frozen=True)
class RuleBasedCalculationInput:
    contract_price: Decimal
    planned_transfer_date: date
    calculation_date: date
    object_transferred: bool
    actual_transfer_date: date | None = None
    client_type: str = "consumer"
    unique_object: bool = False
    manual_review_flags: tuple[str, ...] = ()


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
    unique_object: bool = False
    gross_penalty_amount: Decimal | None = None
    amount_cap: Decimal | None = None
    amount_cap_applied: bool = False
    manual_review_required: bool = False
    manual_review_reasons: list[str] = field(default_factory=list)
    excluded_segments: list[dict[str, Any]] = field(default_factory=list)
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


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + int(months)
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _source_refs(
    raw: object,
    *,
    sources: dict[str, Any],
    title: str,
    required: bool = True,
) -> list[str]:
    if raw in (None, "") and not required:
        return []
    if not isinstance(raw, list) or (required and not raw):
        raise CalculationRuleError(f"{title}: нужны ссылки на правовые источники")
    refs: list[str] = []
    for value in raw:
        ref = str(value or "").strip()
        if not ref:
            raise CalculationRuleError(f"{title}: пустой идентификатор источника")
        if ref not in sources:
            raise CalculationRuleError(
                f"{title}: источник {ref!r} отсутствует в реестре sources"
            )
        if ref not in refs:
            refs.append(ref)
    return refs


def _parse_sources(raw: object) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict) or not raw:
        raise CalculationRuleError("schema v2 требует непустой реестр правовых источников")
    sources: dict[str, dict[str, Any]] = {}
    for raw_code, raw_value in raw.items():
        code = str(raw_code or "").strip()
        if not code or not isinstance(raw_value, dict):
            raise CalculationRuleError("Некорректная запись реестра источников")
        title = str(raw_value.get("title") or "").strip()
        locator = str(raw_value.get("locator") or "").strip()
        url = str(raw_value.get("url") or "").strip()
        if not title:
            raise CalculationRuleError(f"Источник {code}: не задано наименование")
        if not locator:
            raise CalculationRuleError(
                f"Источник {code}: не указан точный пункт/раздел/таблица основания"
            )
        if not url.startswith("https://"):
            raise CalculationRuleError(
                f"Источник {code}: требуется проверяемая HTTPS-ссылка"
            )
        sources[code] = {
            **raw_value,
            "title": title,
            "locator": locator,
            "url": url,
        }
    return sources


def _parse_conditions(raw: dict[str, Any]) -> dict[str, Any]:
    client_types_raw = raw.get("client_types", "*")
    if client_types_raw == "*":
        client_types: set[str] | None = None
    elif isinstance(client_types_raw, list) and client_types_raw:
        client_types = {str(item) for item in client_types_raw}
    else:
        raise CalculationRuleError("client_types должен быть '*' или непустым списком")

    unique_raw = raw.get("unique_object")
    if unique_raw is not None and not isinstance(unique_raw, bool):
        raise CalculationRuleError("unique_object в условиях должен быть true/false/null")
    return {
        "client_types": client_types,
        "unique_object": unique_raw,
    }


def _conditions_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_unique = left["conditions"]["unique_object"]
    right_unique = right["conditions"]["unique_object"]
    if (
        left_unique is not None
        and right_unique is not None
        and left_unique != right_unique
    ):
        return False
    left_types = left["conditions"]["client_types"]
    right_types = right["conditions"]["client_types"]
    if left_types is not None and right_types is not None and not (left_types & right_types):
        return False
    return True


def _applies(item: dict[str, Any], data: RuleBasedCalculationInput) -> bool:
    conditions = item.get("conditions") or {}
    client_types = conditions.get("client_types")
    unique_object = conditions.get("unique_object")
    if client_types is not None and data.client_type not in client_types:
        return False
    if unique_object is not None and bool(data.unique_object) != bool(unique_object):
        return False
    return True


def _parse_period_rules(
    raw_items: object,
    *,
    sources: dict[str, Any],
    title: str,
    value_key: str | None,
) -> list[dict[str, Any]]:
    if raw_items in (None, ""):
        return []
    if not isinstance(raw_items, list):
        raise CalculationRuleError(f"{title} должен быть списком")
    parsed: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise CalculationRuleError(f"{title} #{index + 1}: ожидается объект")
        start = _iso_date(raw.get("start"), f"{title} #{index + 1}: start")
        end = _iso_date(raw.get("end"), f"{title} #{index + 1}: end")
        if end < start:
            raise CalculationRuleError(f"{title} #{index + 1}: end раньше start")
        item = {
            "code": str(raw.get("code") or f"period-{index + 1}"),
            "start": start,
            "end": end,
            "conditions": _parse_conditions(raw),
            "source_refs": _source_refs(
                raw.get("source_refs"),
                sources=sources,
                title=f"{title} #{index + 1}",
            ),
        }
        if value_key:
            value = _decimal(raw.get(value_key), f"{title} #{index + 1}: {value_key}")
            if value < 0:
                raise CalculationRuleError(
                    f"{title} #{index + 1}: {value_key} не может быть отрицательным"
                )
            item[value_key] = value
        parsed.append(item)

    ordered = sorted(parsed, key=lambda item: (item["start"], item["end"], item["code"]))
    for index, previous in enumerate(ordered):
        for current in ordered[index + 1 :]:
            if current["start"] > previous["end"]:
                break
            if _conditions_overlap(previous, current):
                raise CalculationRuleError(
                    f"{title}: пересекающиеся применимые периоды {previous['code']} и {current['code']}"
                )
    return ordered


def _parse_legacy_rule_payload(
    rules: dict[str, Any], *, client_type: str
) -> dict[str, Any]:
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


def _parse_v2_rule_payload(
    rules: dict[str, Any], *, client_type: str
) -> dict[str, Any]:
    sources = _parse_sources(rules.get("sources"))

    formula = rules.get("formula")
    if not isinstance(formula, dict) or formula.get("code") != _SUPPORTED_FORMULA_V2:
        raise CalculationRuleError(
            f"schema v2 требует formula.code={_SUPPORTED_FORMULA_V2!r}"
        )
    formula_refs = _source_refs(
        formula.get("source_refs"),
        sources=sources,
        title="Базовая формула",
    )
    try:
        start_offset = int(formula["delay_start_offset_days"])
    except (KeyError, TypeError, ValueError) as error:
        raise CalculationRuleError("Не задано правило начала периода просрочки") from error
    if start_offset < 0:
        raise CalculationRuleError("Смещение начала просрочки не может быть отрицательным")
    divisor = _decimal(formula.get("divisor"), "Делитель формулы")
    if divisor <= 0:
        raise CalculationRuleError("Делитель формулы должен быть положительным")
    money_quant = _decimal(formula.get("money_quant"), "Шаг округления")
    if money_quant <= 0:
        raise CalculationRuleError("Шаг округления должен быть положительным")
    rounding_name = str(formula.get("rounding_mode") or "")
    if rounding_name not in _SUPPORTED_ROUNDING:
        raise CalculationRuleError("Не задан поддерживаемый режим округления")
    rounding_stage = str(formula.get("rounding_stage") or "")
    if rounding_stage not in {"total", "segment"}:
        raise CalculationRuleError("rounding_stage должен быть total или segment")

    rate_policy = rules.get("rate_policy")
    if not isinstance(rate_policy, dict) or rate_policy.get("mode") != "due_date":
        raise CalculationRuleError(
            "schema v2 допускает только rate_policy.mode='due_date'"
        )
    rate_policy_refs = _source_refs(
        rate_policy.get("source_refs"),
        sources=sources,
        title="Источник базовой ставки",
    )
    coverage_from = _iso_date(
        rate_policy.get("coverage_from"),
        "Справочник ставок: coverage_from",
    )
    coverage_through = _iso_date(
        rate_policy.get("coverage_through"),
        "Справочник ставок: coverage_through",
    )
    if coverage_through < coverage_from:
        raise CalculationRuleError(
            "Справочник ставок: coverage_through раньше coverage_from"
        )

    raw_rates = rules.get("rate_directory")
    if not isinstance(raw_rates, list) or not raw_rates:
        raise CalculationRuleError("Справочник ставок ЦБ пуст")
    rates: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_rates):
        if not isinstance(raw, dict):
            raise CalculationRuleError(f"Ставка ЦБ #{index + 1}: ожидается объект")
        start = _iso_date(raw.get("start"), f"Ставка ЦБ #{index + 1}: start")
        end = _optional_iso_date(raw.get("end"), f"Ставка ЦБ #{index + 1}: end")
        if end is not None and end < start:
            raise CalculationRuleError(f"Ставка ЦБ #{index + 1}: end раньше start")
        rate = _decimal(raw.get("rate"), f"Ставка ЦБ #{index + 1}")
        if rate < 0 or rate > 1:
            raise CalculationRuleError(
                f"Ставка ЦБ #{index + 1} должна быть долей от 0 до 1"
            )
        rates.append(
            {
                "code": str(raw.get("code") or f"cbr-rate-{index + 1}"),
                "start": start,
                "end": end,
                "rate": rate,
                "source_refs": _source_refs(
                    raw.get("source_refs"),
                    sources=sources,
                    title=f"Ставка ЦБ #{index + 1}",
                ),
            }
        )
    rates.sort(key=lambda item: item["start"])
    for previous, current in zip(rates, rates[1:]):
        previous_end = previous["end"] or date.max
        if current["start"] <= previous_end:
            raise CalculationRuleError("Периоды справочника ставок ЦБ пересекаются")

    # The directory must explicitly prove contiguous known coverage. An
    # open-ended last rate may exist in DRAFT, but LEGAL_REVIEWED/PRODUCTION
    # validation still uses the declared coverage_through rather than silently
    # assuming a rate remains unchanged forever.
    cursor = coverage_from
    for item in rates:
        item_end = item["end"] or coverage_through
        if item_end < coverage_from or item["start"] > coverage_through:
            continue
        start = max(item["start"], coverage_from)
        end = min(item_end, coverage_through)
        if start > cursor:
            raise CalculationRuleError(
                "Справочник ставок ЦБ содержит разрыв: "
                f"{cursor.isoformat()}–{(start - timedelta(days=1)).isoformat()}"
            )
        if start < cursor:
            # Overlap was already rejected globally; this only permits an entry
            # to begin before declared coverage_from.
            start = cursor
        if end >= cursor:
            cursor = end + timedelta(days=1)
        if cursor > coverage_through:
            break
    if cursor <= coverage_through:
        raise CalculationRuleError(
            "Справочник ставок ЦБ не покрывает заявленный период до "
            f"{coverage_through.isoformat()}"
        )

    client_types = rules.get("client_types")
    if not isinstance(client_types, dict) or client_type not in client_types:
        raise CalculationRuleError(
            f"Для типа клиента {client_type!r} нет утверждённого правила"
        )
    raw_client = client_types[client_type]
    if not isinstance(raw_client, dict):
        raise CalculationRuleError("Правило типа клиента должно быть объектом")
    multiplier = _decimal(raw_client.get("multiplier"), "Коэффициент типа клиента")
    if multiplier <= 0:
        raise CalculationRuleError("Коэффициент типа клиента должен быть положительным")
    client_refs = _source_refs(
        raw_client.get("source_refs"),
        sources=sources,
        title=f"Тип клиента {client_type}",
    )

    caps = _parse_period_rules(
        rules.get("rate_caps", []),
        sources=sources,
        title="Ограничение ставки",
        value_key="cap",
    )
    moratoria = _parse_period_rules(
        rules.get("moratoria", []),
        sources=sources,
        title="Мораторий",
        value_key=None,
    )

    unique_rule = rules.get("unique_object")
    if not isinstance(unique_rule, dict):
        raise CalculationRuleError("Не задано правило для уникального объекта")
    unique_refs = _source_refs(
        unique_rule.get("source_refs"),
        sources=sources,
        title="Уникальный объект",
    )
    unique_enabled = bool(unique_rule.get("enabled", True))
    unique_multiplier = _decimal(
        unique_rule.get("multiplier"), "Коэффициент уникального объекта"
    )
    unique_cap_percent = _decimal(
        unique_rule.get("amount_cap_percent"), "Лимит уникального объекта"
    )
    if unique_multiplier <= 0:
        raise CalculationRuleError(
            "Коэффициент уникального объекта должен быть положительным"
        )
    if unique_cap_percent <= 0 or unique_cap_percent > 1:
        raise CalculationRuleError(
            "Лимит уникального объекта должен быть долей от 0 до 1"
        )
    try:
        unique_review_months = int(unique_rule.get("manual_review_after_months"))
    except (TypeError, ValueError) as error:
        raise CalculationRuleError(
            "manual_review_after_months уникального объекта должен быть целым"
        ) from error
    if unique_review_months <= 0:
        raise CalculationRuleError(
            "manual_review_after_months уникального объекта должен быть положительным"
        )

    stop_factors: list[dict[str, Any]] = []
    raw_stops = rules.get("stop_factors", [])
    if not isinstance(raw_stops, list):
        raise CalculationRuleError("stop_factors должен быть списком")
    for index, raw in enumerate(raw_stops):
        if not isinstance(raw, dict):
            raise CalculationRuleError(f"Стоп-фактор #{index + 1}: ожидается объект")
        flag = str(raw.get("flag") or "").strip()
        message = str(raw.get("message") or "").strip()
        if not flag or not message:
            raise CalculationRuleError(
                f"Стоп-фактор #{index + 1}: нужны flag и message"
            )
        stop_factors.append(
            {
                "code": str(raw.get("code") or f"stop-{index + 1}"),
                "flag": flag,
                "message": message,
                "source_refs": _source_refs(
                    raw.get("source_refs"),
                    sources=sources,
                    title=f"Стоп-фактор #{index + 1}",
                ),
            }
        )

    return {
        "schema_version": 2,
        "sources": sources,
        "formula_source_refs": formula_refs,
        "rate_policy_source_refs": rate_policy_refs,
        "rate_coverage_from": coverage_from,
        "rate_coverage_through": coverage_through,
        "client_source_refs": client_refs,
        "unique_source_refs": unique_refs,
        "start_offset": start_offset,
        "divisor": divisor,
        "multiplier": multiplier,
        "money_quant": money_quant,
        "rounding_name": rounding_name,
        "rounding": _SUPPORTED_ROUNDING[rounding_name],
        "rounding_stage": rounding_stage,
        "rates": rates,
        "caps": caps,
        "moratoria": moratoria,
        "unique_rule": {
            "enabled": unique_enabled,
            "multiplier": unique_multiplier,
            "amount_cap_percent": unique_cap_percent,
            "manual_review_after_months": unique_review_months,
            "source_refs": unique_refs,
        },
        "stop_factors": stop_factors,
    }


def _parse_rule_payload(rules: dict[str, Any], *, client_type: str) -> dict[str, Any]:
    if not isinstance(rules, dict):
        raise CalculationRuleError("Набор правил должен быть JSON-объектом")
    schema_version = rules.get("schema_version")
    if schema_version == 1:
        return _parse_legacy_rule_payload(rules, client_type=client_type)
    if schema_version == 2:
        return _parse_v2_rule_payload(rules, client_type=client_type)
    raise CalculationRuleError("Неподдерживаемая версия схемы правил")


def _rate_at_due_date(
    rates: list[dict[str, Any]],
    due_date: date,
    *,
    coverage_from: date | None = None,
    coverage_through: date | None = None,
) -> dict[str, Any]:
    if (
        coverage_from is not None
        and coverage_through is not None
        and not (coverage_from <= due_date <= coverage_through)
    ):
        raise CalculationRuleError(
            "Дата исполнения обязательства находится вне подтверждённого "
            "периода справочника ставок ЦБ"
        )
    matches = [
        item
        for item in rates
        if item["start"] <= due_date
        and (item["end"] is None or item["end"] >= due_date)
    ]
    if len(matches) != 1:
        raise CalculationRuleError(
            "Справочник ставок ЦБ не даёт ровно одну ставку на дату исполнения обязательства"
        )
    return matches[0]


def _split_by_caps(
    interval: _DateInterval,
    caps: list[dict[str, Any]],
) -> list[tuple[_DateInterval, dict[str, Any] | None]]:
    relevant = [
        item
        for item in caps
        if not (item["end"] < interval.start or item["start"] > interval.end)
    ]
    boundaries = {interval.start, interval.end + timedelta(days=1)}
    for item in relevant:
        boundaries.add(max(interval.start, item["start"]))
        after = item["end"] + timedelta(days=1)
        if after <= interval.end:
            boundaries.add(after)
        elif item["end"] < interval.end:
            boundaries.add(after)
    points = sorted(boundaries)
    result: list[tuple[_DateInterval, dict[str, Any] | None]] = []
    for left, right in zip(points, points[1:]):
        segment = _DateInterval(left, right - timedelta(days=1))
        matching = [
            item
            for item in relevant
            if item["start"] <= segment.start <= item["end"]
        ]
        if len(matching) > 1:
            raise CalculationRuleError(
                "Для одного сегмента найдено несколько применимых ограничений ставки"
            )
        result.append((segment, matching[0] if matching else None))
    return result


def _legacy_calculate(
    data: RuleBasedCalculationInput,
    *,
    parsed: dict[str, Any],
    price: Decimal,
    period_start: date,
    end_date: date,
    rule_revision_id: int,
    rule_revision_key: str,
    rule_snapshot_sha256: str,
    rule_snapshot: dict[str, Any],
) -> RuleBasedCalculationResult:
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
                    parsed["money_quant"], rounding=parsed["rounding"]
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

    return RuleBasedCalculationResult(
        contract_price=price.quantize(parsed["money_quant"], rounding=parsed["rounding"]),
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
        formula=(
            f"rule={rule_revision_key}; formula={_SUPPORTED_FORMULA}; "
            f"divisor={parsed['divisor']}; multiplier={parsed['multiplier']}; "
            f"chargeable_days={chargeable_days}; segments={len(applied_segments)}"
        ),
        recommended_route=("M1" if chargeable_days > 0 and amount > 0 else "M2"),
        rule_revision_id=int(rule_revision_id),
        rule_revision_key=str(rule_revision_key),
        rule_snapshot_sha256=str(rule_snapshot_sha256),
        rule_snapshot=rule_snapshot,
        applied_segments=applied_segments,
        gross_penalty_amount=amount,
        warning=warning,
    )


class CalculationRuleEngine:
    """Apply one immutable legal rule revision without legal defaults."""

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

        if parsed["schema_version"] == 1:
            return _legacy_calculate(
                data,
                parsed=parsed,
                price=price,
                period_start=period_start,
                end_date=end_date,
                rule_revision_id=rule_revision_id,
                rule_revision_key=rule_revision_key,
                rule_snapshot_sha256=rule_snapshot_sha256,
                rule_snapshot=rule_snapshot,
            )

        if data.unique_object and not parsed["unique_rule"]["enabled"]:
            raise CalculationManualReviewRequired(
                ["Для уникального объекта автоматическая ветка не утверждена"]
            )

        manual_reasons: list[str] = []
        active_flags = {str(item) for item in data.manual_review_flags}
        for stop in parsed["stop_factors"]:
            if stop["flag"] in active_flags:
                manual_reasons.append(stop["message"])

        if data.unique_object:
            threshold = _add_months(
                data.planned_transfer_date,
                parsed["unique_rule"]["manual_review_after_months"],
            )
            if end_date > threshold:
                manual_reasons.append(
                    "Просрочка по уникальному объекту превышает утверждённый порог "
                    f"{parsed['unique_rule']['manual_review_after_months']} месяцев "
                    "для автоматического расчёта"
                )

        if manual_reasons:
            raise CalculationManualReviewRequired(manual_reasons)

        if end_date < period_start:
            total_days = 0
            chargeable: list[_DateInterval] = []
            applicable_moratoria: list[dict[str, Any]] = []
            merged_excluded: list[_DateInterval] = []
            excluded_segments: list[dict[str, Any]] = []
        else:
            base = _DateInterval(period_start, end_date)
            total_days = base.days
            applicable_moratoria = [
                item for item in parsed["moratoria"] if _applies(item, data)
            ]
            excluded_segments = []
            exclusion_intervals: list[_DateInterval] = []
            for item in applicable_moratoria:
                clipped = _clip(
                    _DateInterval(item["start"], item["end"]),
                    base.start,
                    base.end,
                )
                if clipped is None:
                    continue
                exclusion_intervals.append(clipped)
                excluded_segments.append(
                    {
                        "code": item["code"],
                        "start": clipped.start.isoformat(),
                        "end": clipped.end.isoformat(),
                        "days": clipped.days,
                        "source_refs": list(item["source_refs"]),
                    }
                )
            merged_excluded = _merge(exclusion_intervals)
            chargeable = _subtract(base, merged_excluded)

        moratorium_days = sum(item.days for item in merged_excluded)
        chargeable_days = sum(item.days for item in chargeable)

        rate_rule = _rate_at_due_date(
            parsed["rates"],
            data.planned_transfer_date,
            coverage_from=parsed["rate_coverage_from"],
            coverage_through=parsed["rate_coverage_through"],
        )
        base_rate = rate_rule["rate"]
        multiplier = (
            parsed["unique_rule"]["multiplier"]
            if data.unique_object
            else parsed["multiplier"]
        )
        applicable_caps = [item for item in parsed["caps"] if _applies(item, data)]

        applied_segments: list[dict[str, Any]] = []
        raw_total = Decimal("0")
        for interval in chargeable:
            for segment, cap_rule in _split_by_caps(interval, applicable_caps):
                effective_rate = base_rate
                cap_value: Decimal | None = None
                cap_code: str | None = None
                cap_source_refs: list[str] = []
                if cap_rule is not None:
                    cap_value = cap_rule["cap"]
                    effective_rate = min(base_rate, cap_value)
                    cap_code = cap_rule["code"]
                    cap_source_refs = list(cap_rule["source_refs"])
                raw_amount = (
                    price
                    * effective_rate
                    / parsed["divisor"]
                    * Decimal(segment.days)
                    * multiplier
                )
                amount = raw_amount
                if parsed["rounding_stage"] == "segment":
                    amount = raw_amount.quantize(
                        parsed["money_quant"], rounding=parsed["rounding"]
                    )
                raw_total += amount
                applied_segments.append(
                    {
                        "start": segment.start.isoformat(),
                        "end": segment.end.isoformat(),
                        "days": segment.days,
                        "rate": str(effective_rate),
                        "base_rate": str(base_rate),
                        "base_rate_code": rate_rule["code"],
                        "base_rate_source_refs": list(rate_rule["source_refs"]),
                        "cap": str(cap_value) if cap_value is not None else None,
                        "cap_code": cap_code,
                        "cap_source_refs": cap_source_refs,
                        "multiplier": str(multiplier),
                        "amount_before_final_rounding": str(amount),
                    }
                )

        gross_amount = raw_total.quantize(
            parsed["money_quant"], rounding=parsed["rounding"]
        )
        amount_cap: Decimal | None = None
        amount_cap_applied = False
        amount = gross_amount
        if data.unique_object:
            amount_cap = (
                price * parsed["unique_rule"]["amount_cap_percent"]
            ).quantize(parsed["money_quant"], rounding=parsed["rounding"])
            if amount > amount_cap:
                amount = amount_cap
                amount_cap_applied = True

        warning = None
        if total_days == 0:
            warning = "Просрочка на выбранную дату не обнаружена."
        elif chargeable_days == 0:
            warning = "Весь период просрочки исключён утверждёнными правилами расчёта."
        elif amount == 0:
            warning = "Расчёт по утверждённым правилам дал нулевую сумму."

        formula = (
            f"rule={rule_revision_key}; formula={_SUPPORTED_FORMULA_V2}; "
            f"rate_date={data.planned_transfer_date.isoformat()}; "
            f"base_rate={base_rate}; divisor={parsed['divisor']}; "
            f"multiplier={multiplier}; chargeable_days={chargeable_days}; "
            f"segments={len(applied_segments)}"
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
            key_rate=base_rate,
            consumer_multiplier=multiplier,
            client_type=data.client_type,
            penalty_amount=amount,
            formula_version=f"rule-v2:{rule_revision_key}",
            formula=formula,
            recommended_route=("M1" if chargeable_days > 0 and amount > 0 else "M2"),
            rule_revision_id=int(rule_revision_id),
            rule_revision_key=str(rule_revision_key),
            rule_snapshot_sha256=str(rule_snapshot_sha256),
            rule_snapshot=rule_snapshot,
            applied_segments=applied_segments,
            unique_object=bool(data.unique_object),
            gross_penalty_amount=gross_amount,
            amount_cap=amount_cap,
            amount_cap_applied=amount_cap_applied,
            manual_review_required=False,
            manual_review_reasons=[],
            excluded_segments=excluded_segments,
            warning=warning,
        )


__all__ = [
    "CalculationManualReviewRequired",
    "CalculationRuleEngine",
    "CalculationRuleError",
    "RuleBasedCalculationInput",
    "RuleBasedCalculationResult",
]
