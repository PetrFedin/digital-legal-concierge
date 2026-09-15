from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal

from app.domain.calculator.calculation_strategies import (
    formula_requires_client_multiplier,
    require_supported_date_strategy,
    require_supported_formula_strategy,
    require_supported_rounding_strategy,
)
from app.domain.calculator.legal_rule_errors import CalculationRuleUnavailableError
from app.models.calculation_rule import CalculationRuleSet


@dataclass(frozen=True)
class RatePeriodSnapshot:
    id: int
    valid_from: date
    valid_to: date | None
    rate_code: str
    rate_value: Decimal
    source_reference: str | None


@dataclass(frozen=True)
class ExclusionPeriodSnapshot:
    id: int
    date_from: date
    date_to: date
    exclusion_type: str
    source_reference: str | None


@dataclass(frozen=True)
class ResolvedCalculationRule:
    rule_set_id: int
    code: str
    revision: int
    effective_from: date
    effective_to: date | None
    formula_code: str
    formula_parameters: dict | None
    rounding_code: str
    date_rule_id: int
    date_rule_revision: int
    date_strategy_code: str
    date_rule_parameters: dict | None
    client_type_rule_id: int | None
    client_type_revision: int | None
    client_type_code: str | None
    consumer_multiplier: Decimal | None
    rates: tuple[RatePeriodSnapshot, ...]
    exclusions: tuple[ExclusionPeriodSnapshot, ...]
    source_reference: str | None


def resolved_rule_from_orm(rule: CalculationRuleSet) -> ResolvedCalculationRule:
    if rule.id is None:
        raise CalculationRuleUnavailableError("Редакция правил не сохранена")
    if rule.date_rule is None or rule.date_rule.id is None:
        raise CalculationRuleUnavailableError(
            "Для редакции правил не задано правило расчётных дат"
        )

    require_supported_formula_strategy(rule.formula_code)
    require_supported_rounding_strategy(rule.rounding_code)
    require_supported_date_strategy(rule.date_rule.strategy_code)

    client_rule = rule.client_type_rule
    multiplier: Decimal | None = None
    if client_rule is not None:
        multiplier = Decimal(client_rule.consumer_multiplier)
    if formula_requires_client_multiplier(rule.formula_code) and multiplier is None:
        raise CalculationRuleUnavailableError(
            "Для выбранной формулы не задан утверждённый коэффициент типа клиента"
        )

    rates = tuple(
        RatePeriodSnapshot(
            id=int(item.id),
            valid_from=item.valid_from,
            valid_to=item.valid_to,
            rate_code=item.rate_code,
            rate_value=Decimal(item.rate_value),
            source_reference=item.source_reference,
        )
        for item in sorted(
            rule.rate_periods,
            key=lambda item: (item.valid_from, item.valid_to or date.max, item.id or 0),
        )
    )
    if not rates:
        raise CalculationRuleUnavailableError(
            "В утверждённой редакции отсутствуют периоды ставок"
        )

    exclusions = tuple(
        ExclusionPeriodSnapshot(
            id=int(item.id),
            date_from=item.date_from,
            date_to=item.date_to,
            exclusion_type=item.exclusion_type,
            source_reference=item.source_reference,
        )
        for item in sorted(
            rule.exclusion_periods,
            key=lambda item: (item.date_from, item.date_to, item.id or 0),
        )
    )

    return ResolvedCalculationRule(
        rule_set_id=int(rule.id),
        code=rule.code,
        revision=int(rule.revision),
        effective_from=rule.effective_from,
        effective_to=rule.effective_to,
        formula_code=rule.formula_code,
        formula_parameters=rule.formula_parameters,
        rounding_code=rule.rounding_code,
        date_rule_id=int(rule.date_rule.id),
        date_rule_revision=int(rule.date_rule.revision),
        date_strategy_code=rule.date_rule.strategy_code,
        date_rule_parameters=rule.date_rule.parameters,
        client_type_rule_id=int(client_rule.id) if client_rule is not None else None,
        client_type_revision=(
            int(client_rule.revision) if client_rule is not None else None
        ),
        client_type_code=(
            client_rule.client_type_code if client_rule is not None else None
        ),
        consumer_multiplier=multiplier,
        rates=rates,
        exclusions=exclusions,
        source_reference=rule.source_reference,
    )


def canonical_rule_snapshot(rule: ResolvedCalculationRule) -> dict:
    raw = asdict(rule)

    def normalize(value):
        if isinstance(value, dict):
            return {
                str(key): normalize(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        if isinstance(value, (tuple, list)):
            return [normalize(item) for item in value]
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Decimal):
            return format(value, "f")
        return value

    return normalize(raw)


def canonical_rule_snapshot_hash(rule: ResolvedCalculationRule) -> str:
    payload = json.dumps(
        canonical_rule_snapshot(rule),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
