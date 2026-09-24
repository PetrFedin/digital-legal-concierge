from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.db.session import AsyncSessionLocal
from app.domain.calculator.rule_revision_service import (
    CalculationRuleRevisionError,
    CalculationRuleRevisionService,
)


def _rules() -> dict:
    source = "LEGAL"
    return {
        "schema_version": 2,
        "formula": {
            "code": "ddu_delay_penalty_v2",
            "delay_start_offset_days": 1,
            "divisor": "300",
            "money_quant": "0.01",
            "rounding_mode": "ROUND_HALF_UP",
            "rounding_stage": "total",
            "source_refs": [source],
        },
        "rate_policy": {
            "mode": "due_date",
            "coverage_from": "2197-01-01",
            "coverage_through": "2197-12-31",
            "source_refs": [source],
        },
        "rate_directory": [
            {
                "code": "RATE-2197",
                "start": "2197-01-01",
                "end": "2197-12-31",
                "rate": "0.10",
                "source_refs": [source],
            }
        ],
        "moratoria": [],
        "rate_caps": [],
        "client_types": {
            "consumer": {"multiplier": "2", "source_refs": [source]},
            "other": {"multiplier": "1", "source_refs": [source]},
        },
        "unique_object": {
            "enabled": True,
            "multiplier": "1",
            "amount_cap_percent": "0.05",
            "manual_review_after_months": 30,
            "source_refs": [source],
        },
        "stop_factors": [],
        "control_examples": [
            {
                "code": "LIFECYCLE-GREEN",
                "source_refs": [source],
                "input": {
                    "contract_price": "300000",
                    "planned_transfer_date": "2197-01-01",
                    "calculation_date": "2197-01-02",
                    "object_transferred": True,
                    "actual_transfer_date": "2197-01-02",
                    "client_type": "consumer",
                    "unique_object": False,
                },
                "expected": {
                    "penalty_amount": "200.00",
                    "delay_days_total": 1,
                    "delay_days_chargeable": 1,
                    "moratorium_days": 0,
                    "base_rate": "0.10",
                    "amount_cap_applied": False,
                },
            }
        ],
        "sources": {
            source: {
                "title": "Synthetic lifecycle legal source",
                "locator": "Synthetic exact provision",
                "url": "https://example.test/legal-lifecycle",
                "checked_at": "2196-12-31",
            }
        },
    }


@pytest.mark.asyncio
async def test_rule_lifecycle_requires_legal_sha_before_approval_and_production():
    async with AsyncSessionLocal() as db:
        service = CalculationRuleRevisionService(db)
        draft = await service.create_draft(
            revision_key=f"LIFECYCLE-{uuid.uuid4().hex}",
            effective_from=date(2197, 1, 1),
            effective_to=date(2197, 12, 31),
            rules=_rules(),
            note="synthetic lifecycle proof",
            actor_type="admin",
            actor_id=101,
        )
        assert draft.status == "DRAFT"
        assert draft.legal_review_sha256 is None

        with pytest.raises(
            CalculationRuleRevisionError,
            match="юридического подтверждения",
        ):
            await service.approve(
                revision=draft,
                actor_type="superadmin",
                actor_id=201,
            )

        reviewed = await service.confirm_legal_review(
            revision=draft,
            actor_type="lawyer",
            actor_id=301,
            comment="Проверены формула, ставки, ограничения и контрольный пример",
        )
        assert reviewed.status == "LEGAL_REVIEWED"
        assert reviewed.legal_review_sha256 == reviewed.rules_sha256

        approved = await service.approve(
            revision=reviewed,
            actor_type="superadmin",
            actor_id=201,
        )
        assert approved.status == "APPROVED"
        assert approved.published_at is None

        with pytest.raises(
            CalculationRuleRevisionError,
            match="Редактировать можно только DRAFT",
        ):
            await service.update_draft(
                revision_id=approved.id,
                effective_from=approved.effective_from,
                effective_to=approved.effective_to,
                rules=approved.rules,
                note=approved.note,
                expected_updated_at=approved.updated_at,
                actor_type="admin",
                actor_id=101,
            )

        production = await service.publish(
            revision=approved,
            actor_type="superadmin",
            actor_id=201,
        )
        assert production.status == "PRODUCTION"
        assert production.published_at is not None

        resolved = await service.resolve(calculation_date=date(2197, 6, 1))
        assert resolved.id == production.id
        assert resolved.rules_sha256 == resolved.legal_review_sha256

        await db.rollback()


@pytest.mark.asyncio
async def test_return_to_draft_invalidates_prior_legal_and_admin_approval():
    async with AsyncSessionLocal() as db:
        service = CalculationRuleRevisionService(db)
        draft = await service.create_draft(
            revision_key=f"RETURN-{uuid.uuid4().hex}",
            effective_from=date(2198, 1, 1),
            effective_to=date(2198, 12, 31),
            rules={
                **_rules(),
                "rate_policy": {
                    **_rules()["rate_policy"],
                    "coverage_from": "2198-01-01",
                    "coverage_through": "2198-12-31",
                },
                "rate_directory": [
                    {
                        **_rules()["rate_directory"][0],
                        "start": "2198-01-01",
                        "end": "2198-12-31",
                    }
                ],
                "control_examples": [
                    {
                        **_rules()["control_examples"][0],
                        "input": {
                            **_rules()["control_examples"][0]["input"],
                            "planned_transfer_date": "2198-01-01",
                            "calculation_date": "2198-01-02",
                            "actual_transfer_date": "2198-01-02",
                        },
                    }
                ],
            },
            actor_type="admin",
            actor_id=102,
        )
        reviewed = await service.confirm_legal_review(
            revision=draft,
            actor_type="lawyer",
            actor_id=302,
            comment="synthetic legal review",
        )
        approved = await service.approve(
            revision=reviewed,
            actor_type="superadmin",
            actor_id=202,
        )
        returned = await service.return_to_draft(
            revision=approved,
            actor_type="admin",
            actor_id=102,
            comment="Изменилось правовое основание",
        )

        assert returned.status == "DRAFT"
        assert returned.legal_review_sha256 is None
        assert returned.legal_reviewed_at is None
        assert returned.approved_at is None
        assert returned.published_at is None

        await db.rollback()
