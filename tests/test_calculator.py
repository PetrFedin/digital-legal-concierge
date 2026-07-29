from datetime import date
from decimal import Decimal

import pytest

from app.domain.calculator.penalty_calculator import (
    FORMULA_VERSION,
    PenaltyCalculationError,
    PenaltyCalculationInput,
    PenaltyCalculator,
    parse_money,
)


def calculate(**overrides):
    values = {
        "contract_price": Decimal("1000000"),
        "planned_transfer_date": date(2026, 1, 1),
        "calculation_date": date(2026, 1, 11),
        "object_transferred": False,
        "key_rate": Decimal("0.16"),
        "consumer_multiplier": Decimal("2"),
    }
    values.update(overrides)
    return PenaltyCalculator().calculate(PenaltyCalculationInput(**values))


def test_calculator_uses_explicit_date_and_exact_decimal_formula():
    result = calculate()

    assert result.delay_days == 10
    assert result.penalty_amount == Decimal("10666.67")
    assert result.key_rate == Decimal("0.160000")
    assert result.formula_version == FORMULA_VERSION
    assert result.formula == "1000000.00 × 0.160000 / 300 × 10 × 2"


def test_transferred_object_uses_actual_transfer_date_not_calculation_date():
    result = calculate(
        calculation_date=date(2026, 2, 1),
        object_transferred=True,
        actual_transfer_date=date(2026, 1, 6),
    )

    assert result.delay_days == 5
    assert result.penalty_amount == Decimal("5333.33")


def test_zero_delay_is_zero_and_routes_to_manual_review():
    result = calculate(
        planned_transfer_date=date(2026, 1, 11),
        calculation_date=date(2026, 1, 11),
    )

    assert result.delay_days == 0
    assert result.penalty_amount == Decimal("0.00")
    assert result.recommended_route == "M2"
    assert result.warning


@pytest.mark.parametrize(
    "overrides,error_text",
    [
        ({"contract_price": Decimal("0")}, "Стоимость"),
        ({"key_rate": Decimal("16")}, "долей"),
        (
            {
                "object_transferred": True,
                "actual_transfer_date": None,
            },
            "фактическая дата",
        ),
        (
            {
                "object_transferred": False,
                "actual_transfer_date": date(2026, 1, 6),
            },
            "не передаётся",
        ),
        (
            {
                "object_transferred": True,
                "actual_transfer_date": date(2025, 12, 31),
            },
            "раньше договорной",
        ),
        (
            {
                "object_transferred": True,
                "actual_transfer_date": date(2026, 2, 2),
                "calculation_date": date(2026, 2, 1),
            },
            "позже даты расчёта",
        ),
    ],
)
def test_invalid_inputs_are_rejected(overrides, error_text):
    with pytest.raises(PenaltyCalculationError, match=error_text):
        calculate(**overrides)


def test_parse_money_normalizes_user_input_and_rejects_non_finite_values():
    assert parse_money(" 1 234 567,89 ₽ ") == Decimal("1234567.89")
    with pytest.raises(PenaltyCalculationError):
        parse_money("NaN")
    with pytest.raises(PenaltyCalculationError):
        parse_money("0")
