from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.assignment.assignment_engine import AssignmentEngine
from app.domain.cases.assignment_service import CaseAssignmentService
from app.domain.cases.case_service import CaseAssignmentError, CaseService
from app.domain.statuses.case_statuses import CaseStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.models.user import User


@pytest.fixture
async def assignment_db(tmp_path):
    database_path = tmp_path / "assignment-capacity.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        yield session_factory
    finally:
        await engine.dispose()


async def create_user(session, telegram_id: int) -> User:
    user = User(
        telegram_id=telegram_id,
        telegram_username=f"client_{telegram_id}",
        full_name=f"Клиент {telegram_id}",
    )
    session.add(user)
    await session.flush()
    return user


async def create_case(
    session,
    *,
    user: User,
    number: str,
    status: str = CaseStatus.M1_LAWYER_REVIEW.value,
    lawyer_id: int | None = None,
) -> Case:
    case = Case(
        case_number=number,
        client_id=user.id,
        route="M1",
        status=status,
        title=f"Дело {number}",
        assigned_lawyer_id=lawyer_id,
    )
    session.add(case)
    await session.flush()
    return case


async def assignment_audit_count(session, case_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.entity_type == "case",
                AuditLog.entity_id == case_id,
                AuditLog.action == "LAWYER_ASSIGNED",
            )
        )
        or 0
    )


@pytest.mark.asyncio
async def test_lawyer_list_exposes_capacity_and_ignores_closed_cases(assignment_db):
    async with assignment_db() as session:
        user = await create_user(session, 910001)
        busy = Lawyer(
            full_name="Занятый юрист",
            workload_limit=2,
            is_active=True,
        )
        free = Lawyer(
            full_name="Свободный юрист",
            workload_limit=3,
            is_active=True,
        )
        inactive = Lawyer(
            full_name="Неактивный юрист",
            workload_limit=10,
            is_active=False,
        )
        session.add_all([busy, free, inactive])
        await session.flush()

        await create_case(
            session,
            user=user,
            number="CAP-001",
            lawyer_id=busy.id,
        )
        await create_case(
            session,
            user=user,
            number="CAP-002",
            lawyer_id=busy.id,
        )
        await create_case(
            session,
            user=user,
            number="CAP-003",
            status=CaseStatus.M1_CLOSED.value,
            lawyer_id=busy.id,
        )
        await create_case(
            session,
            user=user,
            number="CAP-004",
            lawyer_id=free.id,
        )
        await session.commit()

        lawyers = await CaseAssignmentService(session).list_active_lawyers()

        assert [lawyer["id"] for lawyer in lawyers] == [free.id, busy.id]
        assert lawyers[0]["current_workload"] == 1
        assert lawyers[0]["available_capacity"] == 2
        assert lawyers[0]["is_available"] is True
        assert lawyers[1]["current_workload"] == 2
        assert lawyers[1]["available_capacity"] == 0
        assert lawyers[1]["is_available"] is False


@pytest.mark.asyncio
async def test_assignment_rejects_lawyer_at_capacity(assignment_db):
    async with assignment_db() as session:
        user = await create_user(session, 910002)
        lawyer = Lawyer(
            full_name="Юрист без свободных мест",
            workload_limit=1,
            is_active=True,
        )
        session.add(lawyer)
        await session.flush()

        await create_case(
            session,
            user=user,
            number="CAP-101",
            lawyer_id=lawyer.id,
        )
        target = await create_case(session, user=user, number="CAP-102")
        await session.commit()

        with pytest.raises(CaseAssignmentError, match="достигнут лимит активных дел"):
            await CaseAssignmentService(session).assign_lawyer(
                case=target,
                lawyer_id=lawyer.id,
                actor_id=7001,
            )

        await session.rollback()
        assert target.assigned_lawyer_id is None


@pytest.mark.asyncio
async def test_direct_case_service_cannot_bypass_capacity(assignment_db):
    async with assignment_db() as session:
        user = await create_user(session, 910005)
        lawyer = Lawyer(
            full_name="Юрист канонического ограничения",
            workload_limit=1,
            is_active=True,
        )
        session.add(lawyer)
        await session.flush()

        await create_case(
            session,
            user=user,
            number="CAP-501",
            lawyer_id=lawyer.id,
        )
        target = await create_case(session, user=user, number="CAP-502")
        await session.commit()

        with pytest.raises(CaseAssignmentError, match="достигнут лимит активных дел"):
            await CaseService(session).assign_lawyer(
                case=target,
                lawyer_id=lawyer.id,
                actor_id=7005,
            )

        await session.rollback()
        persisted = await session.get(Case, target.id)
        assert persisted.assigned_lawyer_id is None
        assert await assignment_audit_count(session, target.id) == 0


@pytest.mark.asyncio
async def test_repeated_assignment_remains_idempotent_at_capacity(assignment_db):
    async with assignment_db() as session:
        user = await create_user(session, 910003)
        lawyer = Lawyer(
            full_name="Назначенный юрист",
            workload_limit=1,
            is_active=True,
        )
        session.add(lawyer)
        await session.flush()

        case = await create_case(
            session,
            user=user,
            number="CAP-201",
            lawyer_id=lawyer.id,
        )
        await session.commit()

        assigned = await CaseAssignmentService(session).assign_lawyer(
            case=case,
            lawyer_id=lawyer.id,
            actor_id=7002,
        )
        await session.commit()

        assert assigned.id == case.id
        assert assigned.assigned_lawyer_id == lawyer.id
        assert await assignment_audit_count(session, case.id) == 0


@pytest.mark.asyncio
async def test_successful_assignment_uses_case_service_and_writes_audit(assignment_db):
    async with assignment_db() as session:
        user = await create_user(session, 910004)
        lawyer = Lawyer(
            full_name="Юрист с резервом",
            workload_limit=2,
            is_active=True,
        )
        session.add(lawyer)
        await session.flush()

        target = await create_case(session, user=user, number="CAP-301")
        await session.commit()

        assigned = await CaseAssignmentService(session).assign_lawyer(
            case=target,
            lawyer_id=lawyer.id,
            actor_id=7003,
        )
        await session.commit()

        assert assigned.assigned_lawyer_id == lawyer.id

        audit = next(
            event
            for event in (
                await session.execute(
                    AuditLog.__table__.select().where(
                        AuditLog.entity_type == "case",
                        AuditLog.entity_id == target.id,
                        AuditLog.action == "LAWYER_ASSIGNED",
                    )
                )
            ).mappings()
            if event["action"] == "LAWYER_ASSIGNED"
        )
        assert audit["actor_type"] == "admin"
        assert audit["actor_id"] == 7003
        assert audit["new_value"] == {"assigned_lawyer_id": lawyer.id}


@pytest.mark.asyncio
async def test_auto_assignment_selects_least_loaded_lawyer(assignment_db):
    async with assignment_db() as session:
        target_user = await create_user(session, 910006)
        occupied_user = await create_user(session, 910007)
        busy = Lawyer(
            full_name="Альфа Юрист",
            workload_limit=5,
            is_active=True,
        )
        free = Lawyer(
            full_name="Бета Юрист",
            workload_limit=5,
            is_active=True,
        )
        session.add_all([busy, free])
        await session.flush()

        await create_case(
            session,
            user=occupied_user,
            number="AUTO-601",
            lawyer_id=busy.id,
        )
        target = await create_case(
            session,
            user=target_user,
            number="AUTO-602",
        )
        await session.commit()

        selected = await AssignmentEngine(session).assign_best_lawyer(
            case=target,
            actor_id=7006,
        )
        await session.commit()

        assert selected is not None
        assert selected.id == free.id
        assert target.assigned_lawyer_id == free.id
        assert await assignment_audit_count(session, target.id) == 1


@pytest.mark.asyncio
async def test_repeated_auto_assignment_does_not_replace_current_lawyer(assignment_db):
    async with assignment_db() as session:
        user = await create_user(session, 910008)
        current = Lawyer(
            full_name="Текущий юрист",
            workload_limit=1,
            is_active=True,
        )
        alternative = Lawyer(
            full_name="Свободный альтернативный юрист",
            workload_limit=10,
            is_active=True,
        )
        session.add_all([current, alternative])
        await session.flush()
        case = await create_case(
            session,
            user=user,
            number="AUTO-701",
            lawyer_id=current.id,
        )
        await session.commit()

        selected = await AssignmentEngine(session).assign_best_lawyer(
            case=case,
            actor_id=7007,
        )
        await session.commit()

        assert selected is not None
        assert selected.id == current.id
        assert case.assigned_lawyer_id == current.id
        assert await assignment_audit_count(session, case.id) == 0


@pytest.mark.asyncio
async def test_auto_assignment_returns_none_when_every_lawyer_is_full(assignment_db):
    async with assignment_db() as session:
        target_user = await create_user(session, 910009)
        occupied_user = await create_user(session, 910010)
        lawyer = Lawyer(
            full_name="Юрист без остаточной ёмкости",
            workload_limit=1,
            is_active=True,
        )
        session.add(lawyer)
        await session.flush()
        await create_case(
            session,
            user=occupied_user,
            number="AUTO-801",
            lawyer_id=lawyer.id,
        )
        target = await create_case(
            session,
            user=target_user,
            number="AUTO-802",
        )
        await session.commit()

        selected = await AssignmentEngine(session).assign_best_lawyer(
            case=target,
            actor_id=7008,
        )

        assert selected is None
        assert target.assigned_lawyer_id is None
        assert await assignment_audit_count(session, target.id) == 0
