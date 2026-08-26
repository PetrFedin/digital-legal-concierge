from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select, update

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.domain.cases.assignment_service import CaseAssignmentService
from app.domain.statuses.case_statuses import CaseStatus
from app.models.admin_user import AdminUser
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.models.user import User
from app.security.access_control import ROLE_LAWYER


_DATABASE_URL = str(settings.database_url)
pytestmark = pytest.mark.skipif(
    not _DATABASE_URL.startswith(("postgresql", "postgres")),
    reason="PostgreSQL case assignment capacity contract",
)


def _telegram_id() -> int:
    return 8_700_000_000_000 + (uuid.uuid4().int % 1_000_000_000)


async def _seed() -> tuple[int, int, int]:
    async with AsyncSessionLocal() as db:
        await db.execute(update(Lawyer).values(is_active=False))
        email = f"assignment-{uuid.uuid4().hex}@example.test"
        lawyer = Lawyer(
            full_name="PostgreSQL Assignment Lawyer",
            email=email,
            is_active=True,
            workload_limit=1,
        )
        account = AdminUser(
            full_name="PostgreSQL Assignment Lawyer",
            username=f"assignment-{uuid.uuid4().hex}",
            email=email,
            role=ROLE_LAWYER,
            is_active=True,
        )
        first_client = User(telegram_id=_telegram_id(), full_name="Assignment client A")
        second_client = User(telegram_id=_telegram_id(), full_name="Assignment client B")
        db.add_all([lawyer, account, first_client, second_client])
        await db.flush()

        first_case = Case(
            case_number=f"ASSIGN-A-{uuid.uuid4().hex[:20]}",
            client_id=first_client.id,
            route="M1",
            status=CaseStatus.M1_DOCUMENTS_RECEIVED,
            title="Concurrent assignment A",
        )
        second_case = Case(
            case_number=f"ASSIGN-B-{uuid.uuid4().hex[:20]}",
            client_id=second_client.id,
            route="M1",
            status=CaseStatus.M1_DOCUMENTS_RECEIVED,
            title="Concurrent assignment B",
        )
        db.add_all([first_case, second_case])
        await db.flush()
        result = int(first_case.id), int(second_case.id), int(lawyer.id)
        await db.commit()
        return result


async def _assign(case_id: int, barrier: asyncio.Barrier) -> int | None:
    async with AsyncSessionLocal() as db:
        await barrier.wait()
        try:
            case = await CaseAssignmentService(db).auto_assign_case(
                case_id=case_id,
                actor_type="system",
                actor_id=None,
                comment="PostgreSQL concurrent assignment verification",
                expected_lawyer_id=None,
                expected_status=CaseStatus.M1_DOCUMENTS_RECEIVED.value,
            )
            assigned = int(case.assigned_lawyer_id) if case and case.assigned_lawyer_id else None
            await db.commit()
            return assigned
        except Exception:
            await db.rollback()
            raise


async def _scenario() -> None:
    first_case_id, second_case_id, lawyer_id = await _seed()
    barrier = asyncio.Barrier(2)
    results = await asyncio.gather(
        _assign(first_case_id, barrier),
        _assign(second_case_id, barrier),
    )

    assert sum(item is not None for item in results) == 1
    assert next(item for item in results if item is not None) == lawyer_id

    async with AsyncSessionLocal() as db:
        cases = list(
            (
                await db.execute(
                    select(Case)
                    .where(Case.id.in_([first_case_id, second_case_id]))
                    .order_by(Case.id.asc())
                )
            ).scalars().all()
        )
        assigned = [case for case in cases if case.assigned_lawyer_id is not None]
        unassigned = [case for case in cases if case.assigned_lawyer_id is None]
        assert len(assigned) == 1
        assert len(unassigned) == 1
        assert int(assigned[0].assigned_lawyer_id) == lawyer_id
        assert assigned[0].assigned_at is not None
        assert assigned[0].sla_status == "FIRST_RESPONSE_PENDING"

        active_count = await db.scalar(
            select(func.count(Case.id)).where(
                Case.assigned_lawyer_id == lawyer_id,
                Case.status.notin_(
                    ("CLOSED", "ARCHIVED", "CANCELLED", "COMPLETED", "M1_CLOSED", "M2_CLOSED")
                ),
            )
        )
        assert int(active_count or 0) == 1

        assignment_events = await db.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.entity_type == "case",
                AuditLog.entity_id.in_([first_case_id, second_case_id]),
                AuditLog.action == "case_lawyer_assigned",
            )
        )
        assert int(assignment_events or 0) == 1


def test_concurrent_assignment_respects_last_capacity_slot() -> None:
    asyncio.run(_scenario())
