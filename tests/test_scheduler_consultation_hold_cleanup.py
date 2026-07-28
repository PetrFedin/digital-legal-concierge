from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.user import User
from app.scheduler.jobs import SchedulerJobs


@pytest.fixture
async def scheduler_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'scheduler-hold-cleanup.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def seed_reservation(session, *, suffix: int, expired: bool, booked: bool = False):
    now = datetime.now(timezone.utc)
    user = User(
        telegram_id=995_000 + suffix,
        full_name=f"Клиент cleanup {suffix}",
    )
    lawyer = Lawyer(
        full_name=f"Юрист cleanup {suffix}",
        is_active=True,
    )
    session.add_all([user, lawyer])
    await session.flush()

    case = Case(
        case_number=f"CLEANUP-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=(
            CaseStatus.M2_CONSULTATION_BOOKED.value
            if booked
            else CaseStatus.M2_PAYMENT_PENDING.value
        ),
        title="Консультация",
        next_action=(
            "Ожидайте консультации"
            if booked
            else "Оплатите консультацию"
        ),
    )
    session.add(case)
    await session.flush()

    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        status=(
            ConsultationStatus.BOOKED.value
            if booked
            else ConsultationStatus.PAYMENT_PENDING.value
        ),
        scheduled_at=now + timedelta(days=1),
    )
    session.add(consultation)
    await session.flush()

    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=1, hours=1),
        status="booked" if booked else "held",
        hold_expires_at=(
            None
            if booked
            else now + timedelta(minutes=-1 if expired else 10)
        ),
        held_by_user_id=user.id,
        consultation_id=consultation.id,
    )
    session.add(slot)
    await session.flush()
    consultation.slot_id = slot.id
    await session.commit()
    return case, consultation, slot


@pytest.mark.asyncio
async def test_scheduler_releases_expired_hold_coherently(scheduler_db):
    async with scheduler_db() as session:
        expired_case, expired_consultation, expired_slot = await seed_reservation(
            session,
            suffix=1,
            expired=True,
        )
        active_case, active_consultation, active_slot = await seed_reservation(
            session,
            suffix=2,
            expired=False,
        )
        booked_case, booked_consultation, booked_slot = await seed_reservation(
            session,
            suffix=3,
            expired=False,
            booked=True,
        )

        released = await SchedulerJobs(session).release_unpaid_consultation_slots()
        await session.commit()

        assert released == 1
        await session.refresh(expired_case)
        await session.refresh(expired_consultation)
        await session.refresh(expired_slot)
        assert expired_slot.status == "available"
        assert expired_slot.consultation_id is None
        assert expired_slot.held_by_user_id is None
        assert expired_slot.hold_expires_at is None
        assert expired_consultation.slot_id is None
        assert expired_consultation.lawyer_id is None
        assert expired_consultation.scheduled_at is None
        assert expired_consultation.status == ConsultationStatus.SLOT_PENDING.value
        assert expired_case.status == CaseStatus.M2_SLOT_PENDING.value
        assert expired_case.next_action == "Выберите удобное время консультации"

        await session.refresh(active_case)
        await session.refresh(active_consultation)
        await session.refresh(active_slot)
        assert active_slot.status == "held"
        assert active_consultation.slot_id == active_slot.id
        assert active_case.status == CaseStatus.M2_PAYMENT_PENDING.value

        await session.refresh(booked_case)
        await session.refresh(booked_consultation)
        await session.refresh(booked_slot)
        assert booked_slot.status == "booked"
        assert booked_consultation.slot_id == booked_slot.id
        assert booked_case.status == CaseStatus.M2_CONSULTATION_BOOKED.value

        history_count = int(
            await session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_id == expired_case.id,
                    AuditLog.action == "CONSULTATION_SLOT_HOLD_EXPIRED",
                )
            )
            or 0
        )
        assert history_count == 1


@pytest.mark.asyncio
async def test_scheduler_cleanup_is_idempotent(scheduler_db):
    async with scheduler_db() as session:
        case, consultation, slot = await seed_reservation(
            session,
            suffix=4,
            expired=True,
        )

        first = await SchedulerJobs(session).release_unpaid_consultation_slots()
        second = await SchedulerJobs(session).release_unpaid_consultation_slots()
        await session.commit()

        assert first == 1
        assert second == 0
        await session.refresh(consultation)
        await session.refresh(slot)
        assert consultation.slot_id is None
        assert slot.status == "available"
        history_count = int(
            await session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_id == case.id,
                    AuditLog.action == "CONSULTATION_SLOT_HOLD_EXPIRED",
                )
            )
            or 0
        )
        assert history_count == 1
