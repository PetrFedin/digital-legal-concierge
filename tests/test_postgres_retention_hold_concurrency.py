from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.domain.retention.case_retention_service import (
    CaseRetentionError,
    CaseRetentionService,
    STATUS_COMPLETED,
    STATUS_DISCOVERED,
)
from app.models.admin_user import AdminUser
from app.models.case import Case
from app.models.case_retention import CaseRetentionRecord
from app.models.user import User


_DATABASE_URL = str(settings.database_url)
pytestmark = pytest.mark.skipif(
    not _DATABASE_URL.startswith(("postgresql", "postgres")),
    reason="PostgreSQL retention legal-hold/destructive-execution concurrency contract",
)


def _telegram_id() -> int:
    return 8_700_000_000_000 + (uuid.uuid4().int % 1_000_000_000)


async def _seed_approved_retention() -> tuple[int, int, int]:
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        user = User(
            telegram_id=_telegram_id(),
            full_name="Retention hold race client",
        )
        requester = AdminUser(
            full_name="Retention requester",
            username=f"retention-requester-{uuid.uuid4().hex}",
            email=f"retention-requester-{uuid.uuid4().hex}@example.test",
            role="superadmin,admin",
            is_active=True,
            mfa_enabled=True,
        )
        approver = AdminUser(
            full_name="Retention approver",
            username=f"retention-approver-{uuid.uuid4().hex}",
            email=f"retention-approver-{uuid.uuid4().hex}@example.test",
            role="superadmin,admin",
            is_active=True,
            mfa_enabled=True,
        )
        db.add_all([user, requester, approver])
        await db.flush()

        case = Case(
            case_number=f"RET-HOLD-RACE-{uuid.uuid4().hex[:18]}",
            client_id=user.id,
            route="M1",
            status="M1_CLOSED",
            title="Retention legal-hold race",
            closed_at=now - timedelta(days=31),
            next_action="Дело завершено",
            sla_status="CLOSED",
        )
        db.add(case)
        await db.flush()

        service = CaseRetentionService(db)
        record = await service.request_deletion(
            case_id=int(case.id),
            actor_id=int(requester.id),
            reason="Истёк утверждённый срок хранения закрытого дела",
            now=now,
        )
        record = await service.approve_deletion(
            record_id=int(record.id),
            actor_id=int(approver.id),
            comment="Второй суперадминистратор подтвердил допустимость удаления",
            now=now,
        )
        result = int(case.id), int(record.id), int(approver.id)
        await db.commit()
        return result


async def _set_hold(
    *,
    case_id: int,
    actor_id: int,
    barrier: asyncio.Barrier,
) -> str:
    async with AsyncSessionLocal() as db:
        await barrier.wait()
        try:
            record = await CaseRetentionService(db).set_legal_hold(
                case_id=case_id,
                actor_id=actor_id,
                reason="Получен судебный запрос, содержимое требуется сохранить",
            )
            await db.commit()
            return str(record.status)
        except Exception:
            await db.rollback()
            raise


async def _execute(
    *,
    record_id: int,
    actor_id: int,
    barrier: asyncio.Barrier,
) -> str:
    async with AsyncSessionLocal() as db:
        await barrier.wait()
        try:
            record = await CaseRetentionService(db).execute_deletion(
                record_id=record_id,
                actor_id=actor_id,
            )
            await db.commit()
            return str(record.status)
        except Exception:
            await db.rollback()
            raise


async def _scenario_hold_and_execute_have_one_serializable_winner() -> None:
    case_id, record_id, actor_id = await _seed_approved_retention()
    barrier = asyncio.Barrier(2)

    results = await asyncio.gather(
        _set_hold(case_id=case_id, actor_id=actor_id, barrier=barrier),
        _execute(record_id=record_id, actor_id=actor_id, barrier=barrier),
        return_exceptions=True,
    )

    successes = [item for item in results if not isinstance(item, BaseException)]
    failures = [item for item in results if isinstance(item, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], CaseRetentionError)

    async with AsyncSessionLocal() as db:
        record = await db.get(CaseRetentionRecord, record_id)
        case = await db.get(Case, case_id)
        assert record is not None
        assert case is not None

        if record.status == STATUS_COMPLETED:
            assert record.legal_hold is False
            assert case.content_deleted_at is not None
        else:
            assert record.status == STATUS_DISCOVERED
            assert record.legal_hold is True
            assert case.content_deleted_at is None

        # This is the forbidden outcome PM-036 existed to eliminate.
        assert not (record.legal_hold and case.content_deleted_at is not None)


def test_legal_hold_and_destructive_execution_are_serializable_under_postgres(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "closed_case_retention_days", 30)
    monkeypatch.setattr(settings, "case_retention_execution_timeout_seconds", 900)
    asyncio.run(_scenario_hold_and_execute_have_one_serializable_winner())
