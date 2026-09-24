from __future__ import annotations

import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.domain.calculator.rule_revision_service import (
    CalculationRuleRevisionError,
    CalculationRuleRevisionService,
)
from app.models.audit_log import AuditLog
from app.models.calculation_rule_revision import CalculationRuleRevision


_DATABASE_URL = str(settings.database_url)
pytestmark = pytest.mark.skipif(
    not _DATABASE_URL.startswith(("postgresql", "postgres")),
    reason="PostgreSQL calculation rule production row-lock contract",
)


def _synthetic_rules(rate_code: str) -> dict:
    # Synthetic concurrency fixture only. Not legal/release data.
    source = "SYNTHETIC-SOURCE"
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
            "coverage_from": "2199-01-01",
            "coverage_through": "2199-12-31",
            "source_refs": [source],
        },
        "rate_directory": [
            {
                "code": rate_code,
                "start": "2199-01-01",
                "end": "2199-12-31",
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
                "code": "SYNTHETIC-GREEN",
                "source_refs": [source],
                "input": {
                    "contract_price": "300000",
                    "planned_transfer_date": "2199-01-01",
                    "calculation_date": "2199-01-02",
                    "object_transferred": True,
                    "actual_transfer_date": "2199-01-02",
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
                "title": "Synthetic test source",
                "url": "https://example.test/legal-source",
                "checked_at": "2198-12-31",
            }
        },
    }


async def _seed_overlapping_approved() -> tuple[int, int]:
    suffix = uuid.uuid4().hex[:12]
    async with AsyncSessionLocal() as db:
        service = CalculationRuleRevisionService(db)
        first = await service.create_draft(
            revision_key=f"RACE-A-{suffix}",
            effective_from=date(2199, 1, 1),
            effective_to=date(2199, 12, 31),
            rules=_synthetic_rules(f"RACE-RATE-A-{suffix}"),
            note="synthetic postgres concurrency fixture",
            actor_type="superadmin",
            actor_id=9101,
        )
        second = await service.create_draft(
            revision_key=f"RACE-B-{suffix}",
            effective_from=date(2199, 6, 1),
            effective_to=None,
            rules=_synthetic_rules(f"RACE-RATE-B-{suffix}"),
            note="synthetic postgres concurrency fixture",
            actor_type="superadmin",
            actor_id=9102,
        )
        await db.flush()

        for revision, actor_id in ((first, 9101), (second, 9102)):
            reviewed = await service.confirm_legal_review(
                revision=revision,
                actor_type="lawyer",
                actor_id=actor_id,
                comment="synthetic legal-review gate",
            )
            await service.approve(
                revision=reviewed,
                actor_type="superadmin",
                actor_id=actor_id,
            )

        result = int(first.id), int(second.id)
        await db.commit()
        return result


async def _publish(*, revision_id: int, actor_id: int, barrier: asyncio.Barrier):
    async with AsyncSessionLocal() as db:
        revision = await db.get(CalculationRuleRevision, revision_id)
        assert revision is not None
        await barrier.wait()
        try:
            result = await CalculationRuleRevisionService(db).publish(
                revision=revision,
                actor_type="superadmin",
                actor_id=actor_id,
            )
            key = str(result.revision_key)
            await db.commit()
            return key
        except Exception:
            await db.rollback()
            raise


async def _scenario_overlapping_publications_have_one_winner() -> None:
    first_id, second_id = await _seed_overlapping_approved()
    barrier = asyncio.Barrier(2)

    results = await asyncio.gather(
        _publish(revision_id=first_id, actor_id=9101, barrier=barrier),
        _publish(revision_id=second_id, actor_id=9102, barrier=barrier),
        return_exceptions=True,
    )

    successes = [item for item in results if not isinstance(item, BaseException)]
    failures = [item for item in results if isinstance(item, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], CalculationRuleRevisionError)
    assert "пересекается" in str(failures[0])

    async with AsyncSessionLocal() as db:
        revisions = list(
            (
                await db.execute(
                    select(CalculationRuleRevision).where(
                        CalculationRuleRevision.id.in_([first_id, second_id])
                    )
                )
            ).scalars().all()
        )
        assert sorted(str(item.status) for item in revisions) == ["APPROVED", "PRODUCTION"]
        production = next(
            item for item in revisions if str(item.status) == "PRODUCTION"
        )
        assert production.approved_at is not None
        assert production.legal_reviewed_at is not None
        assert production.published_at is not None
        assert production.published_by_actor_id in {9101, 9102}

        publication_events = await db.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.entity_type == "calculation_rule_revision",
                AuditLog.entity_id.in_([first_id, second_id]),
                AuditLog.action == "CALCULATION_RULE_PUBLISHED",
            )
        )
        assert int(publication_events or 0) == 1


def test_overlapping_rule_publications_have_one_winner_under_postgres() -> None:
    asyncio.run(_scenario_overlapping_publications_have_one_winner())
