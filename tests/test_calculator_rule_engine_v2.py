from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.calculator.rule_engine import (
    CalculationManualReviewRequired,
    CalculationRuleEngine,
    CalculationRuleError,
    RuleBasedCalculationInput,
)
from app.domain.calculator.rule_revision_service import (
    rule_payload_sha256,
    run_control_examples,
    validate_rule_payload,
)


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "docs" / "calculator" / "PM016_V2_RULE_TEMPLATE.json"


def _rules() -> dict:
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


def _calculate(rules: dict, **overrides):
    values = {
        "contract_price": Decimal("10000000"),
        "planned_transfer_date": date(2026, 3, 31),
        "calculation_date": date(2026, 5, 25),
        "object_transferred": True,
        "actual_transfer_date": date(2026, 5, 25),
        "client_type": "consumer",
        "unique_object": False,
        "manual_review_flags": (),
    }
    values.update(overrides)
    return CalculationRuleEngine().calculate(
        RuleBasedCalculationInput(**values),
        rule_revision_id=1602,
        rule_revision_key="PM016-V2-TEST",
        rule_snapshot_sha256=rule_payload_sha256(rules),
        rule_snapshot=rules,
    )


def test_lawyer_reviewed_template_is_complete_and_control_examples_are_green():
    rules = _rules()

    validate_rule_payload(rules)
    outcomes = run_control_examples(rules)

    assert [item["code"] for item in outcomes] == [
        "A-ORDINARY-CONSUMER-2026",
        "B-PP479-PP326-SEGMENTS",
        "C-UNIQUE-OBJECT-5PCT",
    ]
    assert all(item["passed"] for item in outcomes)


def test_key_rate_is_fixed_on_due_date_not_changed_daily_during_delay():
    rules = _rules()

    result = _calculate(
        rules,
        contract_price=Decimal("3000000"),
        planned_transfer_date=date(2026, 2, 13),
        calculation_date=date(2026, 3, 25),
        object_transferred=True,
        actual_transfer_date=date(2026, 3, 25),
    )

    assert result.key_rate == Decimal("0.16")
    assert {Decimal(item["base_rate"]) for item in result.applied_segments} == {
        Decimal("0.16")
    }
    # The CBR directory changes to 15.5% on 16.02 and 15% on 23.03, but
    # neither later value changes the base rate fixed on 13.02.
    assert result.penalty_amount == Decimal("128000.00")


def test_pp326_rate_cap_segments_use_minimum_of_due_rate_and_7_5_percent():
    rules = _rules()

    result = _calculate(
        rules,
        contract_price=Decimal("3000000"),
        planned_transfer_date=date(2023, 7, 24),
        calculation_date=date(2023, 8, 2),
        object_transferred=True,
        actual_transfer_date=date(2023, 8, 2),
    )

    assert result.key_rate == Decimal("0.085")
    assert result.delay_days_chargeable == 9
    assert {Decimal(item["rate"]) for item in result.applied_segments} == {
        Decimal("0.075")
    }
    assert {Decimal(item["cap"]) for item in result.applied_segments} == {
        Decimal("0.075")
    }
    assert result.penalty_amount == Decimal("13500.00")


def test_pp326_standard_moratorium_excludes_days_instead_of_zero_rate():
    rules = _rules()

    result = _calculate(
        rules,
        contract_price=Decimal("3000000"),
        planned_transfer_date=date(2024, 3, 21),
        calculation_date=date(2024, 4, 10),
        object_transferred=True,
        actual_transfer_date=date(2024, 4, 10),
    )

    assert result.delay_days_total == 20
    assert result.delay_days_chargeable == 0
    assert result.moratorium_days == 20
    assert result.penalty_amount == Decimal("0.00")
    assert result.applied_segments == []
    assert result.excluded_segments[0]["code"] == "PP326-P2-STANDARD"


def test_unique_object_applies_no_double_multiplier_and_5_percent_amount_cap():
    rules = _rules()

    result = _calculate(
        rules,
        contract_price=Decimal("20000000"),
        planned_transfer_date=date(2026, 1, 13),
        calculation_date=date(2026, 5, 8),
        object_transferred=True,
        actual_transfer_date=date(2026, 5, 8),
        client_type="consumer",
        unique_object=True,
    )

    assert result.consumer_multiplier == Decimal("1")
    assert result.gross_penalty_amount == Decimal("1226666.67")
    assert result.amount_cap == Decimal("1000000.00")
    assert result.amount_cap_applied is True
    assert result.penalty_amount == Decimal("1000000.00")


def test_unique_object_over_30_months_requires_manual_lawyer_review():
    rules = _rules()

    with pytest.raises(CalculationManualReviewRequired, match="30"):
        _calculate(
            rules,
            planned_transfer_date=date(2023, 1, 1),
            calculation_date=date(2025, 8, 2),
            object_transferred=True,
            actual_transfer_date=date(2025, 8, 2),
            unique_object=True,
        )


def test_explicit_stop_factor_never_gets_guessed_value():
    rules = _rules()

    with pytest.raises(CalculationManualReviewRequired, match="тип участника"):
        _calculate(
            rules,
            manual_review_flags=("client_type_unknown",),
        )


def test_value_source_link_is_mandatory_for_legal_rule():
    rules = _rules()
    broken = deepcopy(rules)
    broken["rate_caps"][0]["source_refs"] = []

    with pytest.raises(CalculationRuleError, match="источники"):
        validate_rule_payload(broken)


def test_cbr_rate_directory_gap_is_rejected_before_approval():
    rules = _rules()
    broken = deepcopy(rules)
    broken["rate_directory"] = [
        item
        for item in broken["rate_directory"]
        if item["code"] != "CBR-10"
    ]

    with pytest.raises(CalculationRuleError, match="разрыв"):
        validate_rule_payload(broken)


def test_due_date_outside_confirmed_cbr_directory_fails_closed():
    rules = _rules()

    with pytest.raises(CalculationRuleError, match="вне подтверждённого периода"):
        _calculate(
            rules,
            planned_transfer_date=date(2015, 12, 31),
            calculation_date=date(2016, 1, 2),
            object_transferred=True,
            actual_transfer_date=date(2016, 1, 2),
        )
