from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from app.domain.calculator.rule_engine import (
    CalculationRuleEngine,
    CalculationRuleError,
    RuleBasedCalculationInput,
)
from app.domain.calculator.rule_revision_service import (
    rule_payload_sha256,
    validate_rule_payload,
)


# Synthetic numbers only. This fixture verifies engine mechanics and MUST NOT be
# treated as a lawyer-approved legal rule revision or production seed data.
def _rules() -> dict:
    return {
        "schema_version": 1,
        "formula_code": "price_rate_divisor_days_multiplier",
        "delay_start_offset_days": 1,
        "divisor": "300",
        "client_types": {
            "consumer": {"multiplier": "2"},
            "business": {"multiplier": "1"},
        },
        "money_quant": "0.01",
        "rounding_mode": "ROUND_HALF_UP",
        "rounding_stage": "total",
        "rates": [
            {
                "code": "TEST-R1",
                "start": "2026-01-01",
                "end": "2026-01-05",
                "rate": "0.10",
            },
            {
                "code": "TEST-R2",
                "start": "2026-01-06",
                "end": None,
                "rate": "0.20",
            },
        ],
        "excluded_periods": [
            {
                "code": "TEST-X1",
                "kind": "test_exclusion",
                "start": "2026-01-04",
                "end": "2026-01-05",
            }
        ],
    }


def _input(**overrides) -> RuleBasedCalculationInput:
    values = {
        "contract_price": Decimal("300000"),
        "planned_transfer_date": date(2026, 1, 1),
        "calculation_date": date(2026, 1, 10),
        "object_transferred": False,
        "actual_transfer_date": None,
        "client_type": "consumer",
    }
    values.update(overrides)
    return RuleBasedCalculationInput(**values)


def _calculate(rules=None, **input_overrides):
    payload = rules or _rules()
    return CalculationRuleEngine().calculate(
        _input(**input_overrides),
        rule_revision_id=17,
        rule_revision_key="TEST-REV-17",
        rule_snapshot_sha256=rule_payload_sha256(payload),
        rule_snapshot=payload,
    )


def test_segmented_rates_and_exclusion_are_evidenced_exactly():
    result = _calculate()

    assert result.delay_days_total == 9
    assert result.moratorium_days == 2
    assert result.delay_days_chargeable == 7
    assert result.delay_days == 7
    assert result.penalty_amount == Decimal("2400.00")
    assert result.key_rate is None
    assert result.recommended_route == "M1"
    assert [segment["days"] for segment in result.applied_segments] == [2, 5]
    assert [segment["rate_code"] for segment in result.applied_segments] == [
        "TEST-R1",
        "TEST-R2",
    ]
    assert result.rule_revision_key == "TEST-REV-17"
    assert result.rule_snapshot_sha256 == rule_payload_sha256(_rules())


def test_early_and_on_time_transfer_are_valid_zero_outcomes():
    early = _calculate(
        object_transferred=True,
        actual_transfer_date=date(2025, 12, 31),
    )
    on_time = _calculate(
        object_transferred=True,
        actual_transfer_date=date(2026, 1, 1),
    )

    for result in (early, on_time):
        assert result.delay_days_total == 0
        assert result.delay_days_chargeable == 0
        assert result.penalty_amount == Decimal("0.00")
        assert result.applied_segments == []
        assert result.recommended_route == "M2"
        assert result.warning


def test_all_excluded_delay_is_zero_chargeable_but_preserves_calendar_delay():
    rules = _rules()
    rules["excluded_periods"] = [
        {
            "code": "TEST-ALL",
            "kind": "test_exclusion",
            "start": "2026-01-02",
            "end": "2026-01-10",
        }
    ]

    result = _calculate(rules=rules)

    assert result.delay_days_total == 9
    assert result.moratorium_days == 9
    assert result.delay_days_chargeable == 0
    assert result.penalty_amount == Decimal("0.00")
    assert result.recommended_route == "M2"


def test_client_type_multiplier_is_rule_data_not_code_default():
    consumer = _calculate(client_type="consumer")
    business = _calculate(client_type="business")

    assert consumer.consumer_multiplier == Decimal("2")
    assert business.consumer_multiplier == Decimal("1")
    assert consumer.penalty_amount == business.penalty_amount * 2


def test_rule_payload_hash_is_canonical_and_reproducible():
    first = _rules()
    second = {
        key: deepcopy(first[key])
        for key in reversed(list(first.keys()))
    }

    assert rule_payload_sha256(first) == rule_payload_sha256(second)
    assert _calculate(first) == _calculate(second)


def test_uncovered_chargeable_days_fail_closed():
    rules = _rules()
    rules["rates"] = [
        {
            "code": "TEST-R1",
            "start": "2026-01-01",
            "end": "2026-01-05",
            "rate": "0.10",
        }
    ]

    with pytest.raises(CalculationRuleError, match="не покрывают весь"):
        _calculate(rules=rules)


def test_overlapping_rate_periods_are_rejected():
    rules = _rules()
    rules["rates"][1]["start"] = "2026-01-05"

    with pytest.raises(CalculationRuleError, match="пересекаются"):
        validate_rule_payload(rules)


def test_future_actual_transfer_date_is_rejected():
    with pytest.raises(CalculationRuleError, match="позже даты расчёта"):
        _calculate(
            object_transferred=True,
            actual_transfer_date=date(2026, 1, 11),
        )


def test_unknown_client_type_has_no_implicit_multiplier():
    with pytest.raises(CalculationRuleError, match="нет утверждённого коэффициента"):
        _calculate(client_type="unknown")
