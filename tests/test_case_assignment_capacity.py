import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cases.assignment_service import CaseAssignmentService
from app.domain.cases.case_service import CaseAssignmentError
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


@pytest.mark.asyncio
async def test_lawyer_list_exposes_capacity_and_ignores_closed_cases(assignment_db):
    async with assignment_db() as session:
        user = await create_user(session, 910001)
        busy = Lawyer(full_name="Занятый юрист", workload_limit=2, is_active=True)
        free = Lawyer(full_name="Свободный юрист", workload_limit=3, is_active=True)
        inactive = Lawyer(full_name="Неактивный юрист", workload_limit=10, is_active=False)
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
        lawyer = Lawyer(full_name="Юрист без свободных мест", workload_limit=1, is_active=True)
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
async def test_repeated_assignment_remains_idempotent_at_capacity(assignment_db):
    async with assignment_db() as session:
        user = await create_user(session, 910003)
        lawyer = Lawyer(full_name="Назначенный юрист", workload_limit=1, is_active=True)
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


@pytest.mark.asyncio
async def test_successful_assignment_uses_case_service_and_writes_audit(assignment_db):
    async with assignment_db() as session:
        user = await create_user(session, 910004)
        lawyer = Lawyer(full_name="Юрист с резервом", workload_limit=2, is_active=True)
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
