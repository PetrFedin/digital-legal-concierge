from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from app.domain.calculator.legal_rule_errors import CalculationRuleValidationError

DATE_STRATEGY_DAY_AFTER_PLANNED_TO_END_INCLUSIVE = (
    "DAY_AFTER_PLANNED_TO_END_INCLUSIVE"
)
FORMULA_PRICE_RATE_DIV_300_DAYS_MULTIPLIER = (
    "PRICE_RATE_DIV_300_DAYS_MULTIPLIER"
)
ROUNDING_PER_SEGMENT_HALF_UP_2 = "PER_SEGMENT_HALF_UP_2"

SUPPORTED_DATE_STRATEGIES = frozenset(
    {DATE_STRATEGY_DAY_AFTER_PLANNED_TO_END_INCLUSIVE}
)
SUPPORTED_FORMULA_STRATEGIES = frozenset(
    {FORMULA_PRICE_RATE_DIV_300_DAYS_MULTIPLIER}
)
SUPPORTED_ROUNDING_STRATEGIES = frozenset({ROUNDING_PER_SEGMENT_HALF_UP_2})

MONEY_QUANT = Decimal("0.01")


def require_supported_date_strategy(code: str) -> str:
    value = str(code or "").strip()
    if value not in SUPPORTED_DATE_STRATEGIES:
        raise CalculationRuleValidationError(
            f"Неподдерживаемая стратегия расчётных дат: {value or '[empty]'}"
        )
    return value


def require_supported_formula_strategy(code: str) -> str:
    value = str(code or "").strip()
    if value not in SUPPORTED_FORMULA_STRATEGIES:
        raise CalculationRuleValidationError(
            f"Неподдерживаемая стратегия формулы: {value or '[empty]'}"
        )
    return value


def require_supported_rounding_strategy(code: str) -> str:
    value = str(code or "").strip()
    if value not in SUPPORTED_ROUNDING_STRATEGIES:
        raise CalculationRuleValidationError(
            f"Неподдерживаемая стратегия округления: {value or '[empty]'}"
        )
    return value


def formula_requires_client_multiplier(code: str) -> bool:
    require_supported_formula_strategy(code)
    return code == FORMULA_PRICE_RATE_DIV_300_DAYS_MULTIPLIER


def calculate_raw_segment_amount(
    *,
    formula_code: str,
    contract_price: Decimal,
    rate_value: Decimal,
    chargeable_days: int,
    consumer_multiplier: Decimal,
) -> Decimal:
    require_supported_formula_strategy(formula_code)
    if formula_code == FORMULA_PRICE_RATE_DIV_300_DAYS_MULTIPLIER:
        return (
            contract_price
            * rate_value
            / Decimal("300")
            * Decimal(chargeable_days)
            * consumer_multiplier
        )
    raise CalculationRuleValidationError(
        f"Стратегия формулы не реализована: {formula_code}"
    )


def round_segment_amount(*, rounding_code: str, value: Decimal) -> Decimal:
    require_supported_rounding_strategy(rounding_code)
    if rounding_code == ROUNDING_PER_SEGMENT_HALF_UP_2:
        return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    raise CalculationRuleValidationError(
        f"Стратегия округления не реализована: {rounding_code}"
    )
