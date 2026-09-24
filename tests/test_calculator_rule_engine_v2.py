from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from app.domain.calculator.rule_catalog_v2 import (
    approved_v2_rule_template,
    client_sources,
    referenced_source_ids,
)
from app.domain.calculator.rule_editor_v2 import (
    delete_item,
    upsert_period,
)
from app.domain.calculator.rule_engine import (
    CalculationRuleEngine,
    CalculationRuleError,
    RuleBasedCalculationInput,
)
from app.domain.calculator.rule_revision_service import (
    CalculationRuleRevisionError,
    rule_payload_sha256,
    validate_draft_payload,
    validate_rule_payload,
)


def _calculate(payload: dict, **overrides):
    values = {
        "contract_price": Decimal("10000000"),
        "planned_transfer_date": date(2026, 3, 31),
        "calculation_date": date(2026, 9, 24),
        "object_transferred": True,
        "actual_transfer_date": date(2026, 5, 25),
        "client_type": "consumer",
        "deadline_confirmed": True,
        "unique_object": False,
        "acceptance_evasion": "no",
        "ddu_signing_date": None,
    }
    values.update(overrides)
    return CalculationRuleEngine().calculate(
        RuleBasedCalculationInput(**values),
        rule_revision_id=25,
        rule_revision_key="DDU-214FZ-2026-09-24",
        rule_snapshot_sha256=rule_payload_sha256(payload),
        rule_snapshot=payload,
    )


def test_lawyer_approved_v2_template_passes_strict_approval_contract():
    payload = approved_v2_rule_template()

    validate_draft_payload(payload)
    validate_rule_payload(payload)

    assert payload["schema_version"] == 2
    assert payload["formula_code"] == "ddu_214fz_art6_due_date_rate_v2"
    assert referenced_source_ids(payload) == set(payload["sources"])


def test_standard_2026_uses_rate_on_contractual_due_date_not_transfer_date():
    payload = approved_v2_rule_template()
    result = _calculate(payload)

    assert result.calculation_branch == "standard"
    assert result.base_rate_date == date(2026, 3, 31)
    assert result.base_rate == Decimal("0.15")
    assert result.delay_days_total == 55
    assert result.delay_days_chargeable == 55
    assert result.moratorium_days == 0
    assert result.consumer_multiplier == Decimal("2")
    assert result.penalty_amount == Decimal("550000.00")
    assert "FZ214_ART6" in result.applied_source_ids
    assert "CBR_KEY_RATE_HISTORY" in result.applied_source_ids
    assert "GK333" in result.applied_source_ids
    assert "INTERNAL_ROUNDING_20260924" in result.applied_source_ids


def test_pp326_cap_and_moratorium_are_separate_and_evidenced():
    payload = approved_v2_rule_template()
    result = _calculate(
        payload,
        contract_price=Decimal("5000000"),
        planned_transfer_date=date(2024, 3, 1),
        actual_transfer_date=date(2024, 4, 10),
    )

    assert result.base_rate == Decimal("0.16")
    assert result.delay_days_total == 40
    assert result.delay_days_chargeable == 20
    assert result.moratorium_days == 20
    assert result.penalty_amount == Decimal("50000.00")
    assert any(item["cap_code"] == "PP326_CAP" for item in result.applied_segments)
    assert "PP326" in result.applied_source_ids


def test_unique_object_uses_separate_branch_and_five_percent_cap():
    payload = approved_v2_rule_template()
    result = _calculate(
        payload,
        planned_transfer_date=date(2024, 12, 31),
        actual_transfer_date=date(2026, 6, 30),
        unique_object=True,
        ddu_signing_date=date(2023, 8, 1),
    )

    assert result.calculation_branch == "unique"
    assert result.penalty_cap_applied is True
    assert result.consumer_multiplier == Decimal("1")
    assert result.penalty_amount == Decimal("500000.00")
    assert "FZ421_ART2" in result.applied_source_ids
    assert "PP326" in result.applied_source_ids


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("deadline_confirmed", False, "срок"),
        ("unique_object", None, "уникальным"),
        ("acceptance_evasion", "unknown", "приёмки"),
    ],
)
def test_uncertain_legal_facts_fail_closed(field, value, match):
    payload = approved_v2_rule_template()

    with pytest.raises(CalculationRuleError, match=match):
        _calculate(payload, **{field: value})


def test_clearing_value_removes_only_source_that_becomes_unused():
    payload = approved_v2_rule_template()
    payload = upsert_period(
        payload,
        section="excluded_periods",
        code="TEST-ONLY",
        start="2026-01-01",
        end="2026-01-02",
        source_id="TEST_ONLY_SOURCE",
        source_title="Тестовый источник",
        source_authority="Тестовый орган",
        source_url="https://example.com/legal-source",
    )
    assert "TEST_ONLY_SOURCE" in payload["sources"]

    cleared = delete_item(
        payload,
        section="excluded_period",
        code="TEST-ONLY",
    )

    assert "TEST_ONLY_SOURCE" not in cleared["sources"]
    assert "FZ214_ART6" in cleared["sources"]
    validate_draft_payload(cleared)


def test_shared_source_survives_until_last_reference_is_removed():
    payload = approved_v2_rule_template()
    assert "PP326" in payload["sources"]

    first = delete_item(
        payload,
        section="excluded_period",
        code="PP326_ART6_PART2",
    )
    assert "PP326" in first["sources"]

    second = delete_item(
        first,
        section="rate_cap",
        code="PP326_CAP",
    )
    assert "PP326" in second["sources"]

    third = delete_item(
        second,
        section="unique_excluded_period",
        code="PP326_ART6_PART2_1",
    )
    assert "PP326" not in third["sources"]


def test_incomplete_draft_can_be_saved_but_cannot_be_approved():
    payload = approved_v2_rule_template()
    cleared = delete_item(payload, section="base_rate")

    validate_draft_payload(cleared)

    with pytest.raises(CalculationRuleRevisionError):
        validate_rule_payload(cleared)


def test_client_source_projection_exposes_only_requested_snapshot_sources():
    payload = approved_v2_rule_template()
    projected = client_sources(
        payload,
        {"FZ214_ART6", "CBR_KEY_RATE_HISTORY"},
    )

    ids = {item["id"] for item in projected}
    assert ids == {"FZ214_ART6", "CBR_KEY_RATE_HISTORY"}
    assert all(str(item.get("url") or "").startswith("https://") for item in projected)
