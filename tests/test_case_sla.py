from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cases.assignment_service import CaseAssignmentService
from app.domain.cases.sla_service import (
    CaseSLAService,
    SLA_ACTION_OVERDUE,
    SLA_ACTION_PENDING,
    SLA_FIRST_RESPONSE_OVERDUE,
    SLA_FIRST_RESPONSE_PENDING,
    SLA_NOT_STARTED,
    SLA_PAUSED,
)
from app.models import Base
from app.models.admin_user import AdminUser
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.models.notification import Notification
from app.models.user import User
from app.system.settings_service import SettingsService


async def create_database(tmp_path, name: str):
    path = tmp_path / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, factory


async def create_context(session, *, suffix: int):
    user = User(
        telegram_id=1110000 + suffix,
        full_name=f"Клиент SLA {suffix}",
    )
    lawyer = Lawyer(
        full_name=f"Юрист SLA {suffix}",
        email=f"sla-lawyer-{suffix}@example.com",
        telegram_id=1120000 + suffix,
        is_active=True,
        workload_limit=10,
    )
    second_lawyer = Lawyer(
        full_name=f"Второй юрист SLA {suffix}",
        email=f"sla-lawyer-second-{suffix}@example.com",
        telegram_id=1130000 + suffix,
        is_active=True,
        workload_limit=10,
    )
    admin = AdminUser(
        full_name=f"Администратор SLA {suffix}",
        username=f"sla-admin-{suffix}",
        email=f"sla-admin-{suffix}@example.com",
        telegram_id=1140000 + suffix,
        password_hash="test-password-hash",
        role="admin",
        is_active=True,
    )
    session.add_all([user, lawyer, second_lawyer, admin])
    await session.flush()
    case = Case(
        case_number=f"SLA-{suffix}",
        client_id=user.id,
        route="M1",
        status="M1_LAWYER_REVIEW",
        title="Проверка SLA",
    )
    session.add(case)
    await session.flush()
    await SettingsService(session).bootstrap_defaults()
    return {
        "user": user,
        "lawyer": lawyer,
        "second_lawyer": second_lawyer,
        "admin": admin,
        "case": case,
    }


@pytest.mark.asyncio
async def test_assignment_starts_first_response_sla(tmp_path):
    engine, factory = await create_database(tmp_path, "sla-assignment.db")
    async with factory() as session:
        context = await create_context(session, suffix=1)
        before = datetime.now(timezone.utc)
        case = await CaseAssignmentService(session).assign_case(
            case_id=context["case"].id,
            lawyer_id=context["lawyer"].id,
            actor_type="admin",
            actor_id=context["admin"].id,
            comment="Назначение для проверки первой реакции",
        )
        await session.commit()

        assert case.assigned_lawyer_id == context["lawyer"].id
        assert case.sla_status == SLA_FIRST_RESPONSE_PENDING
        assert case.assigned_at is not None
        assert case.first_lawyer_response_at is None
        assert case.last_lawyer_activity_at is None
        assert case.escalation_level == 0
        assert case.sla_due_at is not None
        due = case.sla_due_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        assert before + timedelta(hours=3, minutes=59) <= due
        assert due <= before + timedelta(hours=4, minutes=1)

        sla_events = (
            await session.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_id == case.id,
                    AuditLog.action == "CASE_SLA_STARTED",
                )
            )
        ).scalar_one()
        assert sla_events == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_reassignment_restarts_first_response_sla(tmp_path):
    engine, factory = await create_database(tmp_path, "sla-reassignment.db")
    async with factory() as session:
        context = await create_context(session, suffix=2)
        service = CaseAssignmentService(session)
        case = await service.assign_case(
            case_id=context["case"].id,
            lawyer_id=context["lawyer"].id,
            actor_type="admin",
            actor_id=context["admin"].id,
        )
        await session.flush()
        case.first_lawyer_response_at = datetime.now(timezone.utc)
        case.last_lawyer_activity_at = datetime.now(timezone.utc)
        case.sla_status = SLA_ACTION_OVERDUE
        case.escalation_level = 3
        old_assigned_at = case.assigned_at
        await session.commit()

        case = await service.assign_case(
            case_id=case.id,
            lawyer_id=context["second_lawyer"].id,
            actor_type="admin",
            actor_id=context["admin"].id,
            comment="Переназначение после просрочки",
        )
        await session.commit()

        assert case.assigned_lawyer_id == context["second_lawyer"].id
        assert case.sla_status == SLA_FIRST_RESPONSE_PENDING
        assert case.first_lawyer_response_at is None
        assert case.last_lawyer_activity_at is None
        assert case.escalation_level == 0
        assert case.assigned_at != old_assigned_at

    await engine.dispose()


@pytest.mark.asyncio
async def test_unassignment_clears_sla(tmp_path):
    engine, factory = await create_database(tmp_path, "sla-unassign.db")
    async with factory() as session:
        context = await create_context(session, suffix=3)
        service = CaseAssignmentService(session)
        case = await service.assign_case(
            case_id=context["case"].id,
            lawyer_id=context["lawyer"].id,
            actor_type="admin",
            actor_id=context["admin"].id,
        )
        await session.commit()

        case = await service.unassign_case(
            case_id=case.id,
            actor_type="admin",
            actor_id=context["admin"].id,
            comment="Снятие назначения",
        )
        await session.commit()

        assert case.assigned_lawyer_id is None
        assert case.assigned_at is None
        assert case.first_lawyer_response_at is None
        assert case.last_lawyer_activity_at is None
        assert case.sla_due_at is None
        assert case.sla_status == SLA_NOT_STARTED
        assert case.escalation_level == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_lawyer_activity_records_first_response_and_next_action(tmp_path):
    engine, factory = await create_database(tmp_path, "sla-activity.db")
    async with factory() as session:
        context = await create_context(session, suffix=4)
        case = await CaseAssignmentService(session).assign_case(
            case_id=context["case"].id,
            lawyer_id=context["lawyer"].id,
            actor_type="admin",
            actor_id=context["admin"].id,
        )
        await session.commit()

        case.status = "M1_ACCEPTED"
        case = await CaseSLAService(session).record_lawyer_activity(
            case=case,
            lawyer_id=context["lawyer"].id,
            action="M1_CASE_ACCEPTED",
            comment="Дело принято в работу",
        )
        await session.commit()

        assert case.first_lawyer_response_at is not None
        assert case.last_lawyer_activity_at is not None
        assert case.sla_status == SLA_ACTION_PENDING
        assert case.sla_due_at is not None
        assert case.escalation_level == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_external_waiting_status_pauses_sla(tmp_path):
    engine, factory = await create_database(tmp_path, "sla-pause.db")
    async with factory() as session:
        context = await create_context(session, suffix=5)
        case = await CaseAssignmentService(session).assign_case(
            case_id=context["case"].id,
            lawyer_id=context["lawyer"].id,
            actor_type="admin",
            actor_id=context["admin"].id,
        )
        await session.commit()

        case.status = "M1_DOCS_REQUESTED"
        case = await CaseSLAService(session).record_lawyer_activity(
            case=case,
            lawyer_id=context["lawyer"].id,
            action="DOCUMENTS_REQUESTED",
            comment="Запрошены дополнительные документы",
        )
        await session.commit()

        assert case.first_lawyer_response_at is not None
        assert case.sla_status == SLA_PAUSED
        assert case.sla_due_at is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_scheduler_escalates_and_deduplicates_until_next_deadline(tmp_path):
    engine, factory = await create_database(tmp_path, "sla-escalation.db")
    async with factory() as session:
        context = await create_context(session, suffix=6)
        case = await CaseAssignmentService(session).assign_case(
            case_id=context["case"].id,
            lawyer_id=context["lawyer"].id,
            actor_type="admin",
            actor_id=context["admin"].id,
        )
        case.sla_due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()

        first = await CaseSLAService(session).escalate_overdue_cases()
        await session.commit()
        await session.refresh(case)
        assert first["escalated_count"] == 1
        assert case.sla_status == SLA_FIRST_RESPONSE_OVERDUE
        assert case.escalation_level == 1

        immediate = await CaseSLAService(session).escalate_overdue_cases()
        await session.commit()
        assert immediate["escalated_count"] == 0

        first_notifications = (
            await session.execute(
                select(func.count(Notification.id)).where(
                    Notification.event_code
                    == "CASE_SLA_FIRST_RESPONSE_OVERDUE"
                )
            )
        ).scalar_one()
        assert first_notifications == 2

        case.sla_due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()
        second = await CaseSLAService(session).escalate_overdue_cases()
        await session.commit()
        await session.refresh(case)

        assert second["escalated_count"] == 1
        assert case.escalation_level == 2
        second_notifications = (
            await session.execute(
                select(func.count(Notification.id)).where(
                    Notification.event_code
                    == "CASE_SLA_FIRST_RESPONSE_OVERDUE"
                )
            )
        ).scalar_one()
        assert second_notifications == 4

    await engine.dispose()


@pytest.mark.asyncio
async def test_acknowledge_overdue_sets_new_deadline_and_admin_audit(tmp_path):
    engine, factory = await create_database(tmp_path, "sla-ack.db")
    async with factory() as session:
        context = await create_context(session, suffix=7)
        case = await CaseAssignmentService(session).assign_case(
            case_id=context["case"].id,
            lawyer_id=context["lawyer"].id,
            actor_type="admin",
            actor_id=context["admin"].id,
        )
        case.sla_due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()
        await CaseSLAService(session).escalate_overdue_cases()
        await session.commit()

        case = await CaseSLAService(session).acknowledge_overdue(
            case_id=case.id,
            actor_id=context["admin"].id,
            comment="Юрист подтвердил получение, установлен новый срок",
        )
        await session.commit()

        assert case.sla_status == SLA_FIRST_RESPONSE_PENDING
        assert case.sla_due_at is not None
        assert case.escalation_level == 0

        audit = (
            await session.execute(
                select(AuditLog)
                .where(
                    AuditLog.entity_id == case.id,
                    AuditLog.action == "CASE_SLA_ACKNOWLEDGED",
                )
                .order_by(AuditLog.id.desc())
            )
        ).scalars().first()
        assert audit.actor_id == context["admin"].id

    await engine.dispose()
