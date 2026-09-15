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
    reason="PostgreSQL calculation rule approval row-lock contract",
)


def _synthetic_rules(rate_code: str) -> dict:
    # Synthetic concurrency fixture only. Not legal/release data.
    return {
        "schema_version": 1,
        "formula_code": "price_rate_divisor_days_multiplier",
        "delay_start_offset_days": 1,
        "divisor": "300",
        "client_types": {"consumer": {"multiplier": "2"}},
        "money_quant": "0.01",
        "rounding_mode": "ROUND_HALF_UP",
        "rounding_stage": "total",
        "rates": [
            {
                "code": rate_code,
                "start": "2199-01-01",
                "end": None,
                "rate": "0.10",
            }
        ],
        "excluded_periods": [],
    }


async def _seed_overlapping_drafts() -> tuple[int, int]:
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
        result = int(first.id), int(second.id)
        await db.commit()
        return result


async def _approve(*, revision_id: int, actor_id: int, barrier: asyncio.Barrier):
    async with AsyncSessionLocal() as db:
        revision = await db.get(CalculationRuleRevision, revision_id)
        assert revision is not None
        await barrier.wait()
        try:
            result = await CalculationRuleRevisionService(db).approve(
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


async def _scenario_overlapping_approvals_have_one_winner() -> None:
    first_id, second_id = await _seed_overlapping_drafts()
    barrier = asyncio.Barrier(2)

    results = await asyncio.gather(
        _approve(revision_id=first_id, actor_id=9101, barrier=barrier),
        _approve(revision_id=second_id, actor_id=9102, barrier=barrier),
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
        assert sorted(str(item.status) for item in revisions) == ["APPROVED", "DRAFT"]
        approved = next(item for item in revisions if str(item.status) == "APPROVED")
        assert approved.approved_at is not None
        assert approved.approved_by_actor_id in {9101, 9102}

        approval_events = await db.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.entity_type == "calculation_rule_revision",
                AuditLog.entity_id.in_([first_id, second_id]),
                AuditLog.action == "CALCULATION_RULE_APPROVED",
            )
        )
        assert int(approval_events or 0) == 1


def test_overlapping_rule_approvals_have_one_winner_under_postgres() -> None:
    asyncio.run(_scenario_overlapping_approvals_have_one_winner())
