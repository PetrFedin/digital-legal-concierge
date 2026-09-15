from datetime import date
from decimal import Decimal

import pytest

from app.domain.calculator.calculation_period_service import (
    CalculationPeriodService,
    SegmentedCalculationInput,
)
from app.domain.calculator.calculation_rule_snapshot import (
    ExclusionPeriodSnapshot,
    RatePeriodSnapshot,
    ResolvedCalculationRule,
    canonical_rule_snapshot_hash,
)
from app.domain.calculator.calculation_strategies import (
    DATE_STRATEGY_DAY_AFTER_PLANNED_TO_END_INCLUSIVE,
    FORMULA_PRICE_RATE_DIV_300_DAYS_MULTIPLIER,
    ROUNDING_PER_SEGMENT_HALF_UP_2,
)
from app.domain.calculator.legal_rule_errors import (
    CalculationRuleAmbiguousError,
    CalculationRuleUnavailableError,
)
from app.domain.calculator.penalty_calculator import PenaltyCalculationError


# Values in this module are synthetic engine fixtures. They are deliberately
# not production legal rates, moratoriums or approved legal advice.
def rule(*, rates, exclusions=()) -> ResolvedCalculationRule:
    return ResolvedCalculationRule(
        rule_set_id=91,
        code="TEST_ONLY",
        revision=7,
        effective_from=date(2020, 1, 1),
        effective_to=None,
        formula_code=FORMULA_PRICE_RATE_DIV_300_DAYS_MULTIPLIER,
        formula_parameters=None,
        rounding_code=ROUNDING_PER_SEGMENT_HALF_UP_2,
        date_rule_id=31,
        date_rule_revision=2,
        date_strategy_code=DATE_STRATEGY_DAY_AFTER_PLANNED_TO_END_INCLUSIVE,
        date_rule_parameters=None,
        client_type_rule_id=41,
        client_type_revision=3,
        client_type_code="TEST_CLIENT",
        consumer_multiplier=Decimal("2"),
        rates=tuple(rates),
        exclusions=tuple(exclusions),
        source_reference="synthetic-test-fixture",
    )


def rate(identifier, start, end, value):
    return RatePeriodSnapshot(
        id=identifier,
        valid_from=start,
        valid_to=end,
        rate_code=f"TEST_RATE_{identifier}",
        rate_value=Decimal(value),
        source_reference="synthetic-test-fixture",
    )


def exclusion(identifier, start, end):
    return ExclusionPeriodSnapshot(
        id=identifier,
        date_from=start,
        date_to=end,
        exclusion_type="TEST_EXCLUSION",
        source_reference="synthetic-test-fixture",
    )


def data(**overrides):
    values = {
        "contract_price": Decimal("1000000"),
        "planned_transfer_date": date(2026, 1, 1),
        "calculation_date": date(2026, 1, 11),
        "object_transferred": False,
        "actual_transfer_date": None,
    }
    values.update(overrides)
    return SegmentedCalculationInput(**values)


def test_single_rate_builds_one_exact_segment():
    result = CalculationPeriodService().calculate(
        data=data(),
        rule=rule(
            rates=(rate(1, date(2026, 1, 2), None, "0.10"),),
        ),
    )

    assert result.delay_days_total == 10
    assert result.delay_days_chargeable == 10
    assert result.moratorium_days == 0
    assert result.penalty_amount == Decimal("6666.67")
    assert len(result.segments) == 1
    assert result.segments[0].period_from == date(2026, 1, 2)
    assert result.segments[0].period_to == date(2026, 1, 11)
    assert result.segments[0].days_total == 10


def test_rate_boundary_does_not_double_count_a_day():
    result = CalculationPeriodService().calculate(
        data=data(),
        rule=rule(
            rates=(
                rate(1, date(2026, 1, 2), date(2026, 1, 6), "0.10"),
                rate(2, date(2026, 1, 7), None, "0.20"),
            ),
        ),
    )

    assert [item.days_total for item in result.segments] == [5, 5]
    assert [item.rate_period_id for item in result.segments] == [1, 2]
    assert result.delay_days_total == 10
    assert result.delay_days_chargeable == 10
    assert result.penalty_amount == Decimal("10000.00")


def test_exclusion_is_segmented_and_never_charged_twice():
    result = CalculationPeriodService().calculate(
        data=data(),
        rule=rule(
            rates=(rate(1, date(2026, 1, 2), None, "0.10"),),
            exclusions=(
                exclusion(10, date(2026, 1, 5), date(2026, 1, 7)),
            ),
        ),
    )

    assert [item.days_total for item in result.segments] == [3, 3, 4]
    assert [item.days_excluded for item in result.segments] == [0, 3, 0]
    assert result.delay_days_total == 10
    assert result.delay_days_chargeable == 7
    assert result.moratorium_days == 3
    assert result.penalty_amount == Decimal("4666.67")
    assert result.segments[1].exclusion_evidence[0]["id"] == 10


def test_overlapping_exclusions_are_unioned_not_double_counted():
    result = CalculationPeriodService().calculate(
        data=data(),
        rule=rule(
            rates=(rate(1, date(2026, 1, 2), None, "0.10"),),
            exclusions=(
                exclusion(10, date(2026, 1, 4), date(2026, 1, 7)),
                exclusion(11, date(2026, 1, 6), date(2026, 1, 9)),
            ),
        ),
    )

    assert result.delay_days_total == 10
    assert result.moratorium_days == 6
    assert result.delay_days_chargeable == 4
    assert sum(item.days_total for item in result.segments) == 10


def test_full_exclusion_returns_zero_without_fabricating_chargeable_days():
    result = CalculationPeriodService().calculate(
        data=data(),
        rule=rule(
            rates=(rate(1, date(2026, 1, 2), None, "0.10"),),
            exclusions=(
                exclusion(10, date(2026, 1, 2), date(2026, 1, 11)),
            ),
        ),
    )

    assert result.delay_days_total == 10
    assert result.delay_days_chargeable == 0
    assert result.moratorium_days == 10
    assert result.penalty_amount == Decimal("0.00")
    assert result.recommended_route == "M2"


def test_missing_rate_for_chargeable_period_fails_closed():
    with pytest.raises(CalculationRuleUnavailableError):
        CalculationPeriodService().calculate(
            data=data(),
            rule=rule(
                rates=(
                    rate(1, date(2026, 1, 2), date(2026, 1, 5), "0.10"),
                ),
            ),
        )


def test_overlapping_rates_fail_closed_even_if_snapshot_was_corrupted():
    with pytest.raises(CalculationRuleAmbiguousError):
        CalculationPeriodService().calculate(
            data=data(),
            rule=rule(
                rates=(
                    rate(1, date(2026, 1, 2), date(2026, 1, 8), "0.10"),
                    rate(2, date(2026, 1, 7), None, "0.20"),
                ),
            ),
        )


def test_early_and_on_time_transfer_remain_valid_zero_delay_outcomes():
    approved = rule(rates=(rate(1, date(2020, 1, 1), None, "0.10"),))

    early = CalculationPeriodService().calculate(
        data=data(
            planned_transfer_date=date(2026, 1, 10),
            object_transferred=True,
            actual_transfer_date=date(2026, 1, 5),
        ),
        rule=approved,
    )
    on_time = CalculationPeriodService().calculate(
        data=data(
            planned_transfer_date=date(2026, 1, 10),
            object_transferred=True,
            actual_transfer_date=date(2026, 1, 10),
        ),
        rule=approved,
    )

    assert early.delay_days_total == 0
    assert early.penalty_amount == Decimal("0.00")
    assert on_time.delay_days_total == 0
    assert on_time.penalty_amount == Decimal("0.00")


def test_actual_transfer_after_calculation_date_is_rejected():
    with pytest.raises(PenaltyCalculationError):
        CalculationPeriodService().calculate(
            data=data(
                object_transferred=True,
                actual_transfer_date=date(2026, 1, 12),
            ),
            rule=rule(rates=(rate(1, date(2026, 1, 2), None, "0.10"),)),
        )


def test_rule_snapshot_hash_is_deterministic_for_same_rule():
    approved = rule(
        rates=(rate(1, date(2026, 1, 2), None, "0.10"),),
        exclusions=(exclusion(2, date(2026, 1, 4), date(2026, 1, 5)),),
    )

    assert canonical_rule_snapshot_hash(approved) == canonical_rule_snapshot_hash(
        approved
    )
