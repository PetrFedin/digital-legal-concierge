from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.availability_service import (
    ConsultationAvailabilityService,
)
from app.domain.consultations.booking_service import ConsultationBookingService
from app.domain.consultations.client_schedule_service import (
    ClientConsultationConflictError,
)
from app.domain.consultations.consultation_service import ConsultationSlotError
from app.domain.consultations.reschedule_service import (
    ConsultationRescheduleService,
)
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.user import User


@pytest.fixture
async def client_schedule_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'client-schedule.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def seed_client_with_busy_interval(session, *, suffix: int):
    now = datetime.now(timezone.utc)
    user = User(
        telegram_id=1_006_000 + suffix,
        full_name=f"Клиент конфликтов {suffix}",
    )
    lawyer = Lawyer(
        full_name=f"Юрист конфликтов {suffix}",
        is_active=True,
        workload_limit=10,
    )
    session.add_all([user, lawyer])
    await session.flush()

    target_window = await ConsultationAvailabilityService(session).create_window(
        lawyer_id=lawyer.id,
        starts_at=now + timedelta(days=3),
        ends_at=now + timedelta(days=3, hours=2),
        allowed_durations=(45,),
        actor_type="admin",
        actor_id=None,
        now=now,
    )
    busy_case = Case(
        case_number=f"BUSY-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_CONSULTATION_BOOKED.value,
    )
    target_case = Case(
        case_number=f"TARGET-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_SLOT_PENDING.value,
    )
    session.add_all([busy_case, target_case])
    await session.flush()
    busy_consultation = Consultation(
        case_id=busy_case.id,
        lawyer_id=lawyer.id,
        status=ConsultationStatus.BOOKED.value,
        scheduled_at=target_window.starts_at,
    )
    target_consultation = Consultation(
        case_id=target_case.id,
        status=ConsultationStatus.SLOT_PENDING.value,
        consultation_type="online",
    )
    session.add_all([busy_consultation, target_consultation])
    await session.flush()
    busy_slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        case_id=busy_case.id,
        starts_at=target_window.starts_at,
        ends_at=target_window.starts_at + timedelta(minutes=45),
        status="booked",
        held_by_user_id=user.id,
        consultation_id=busy_consultation.id,
    )
    session.add(busy_slot)
    await session.flush()
    busy_consultation.slot_id = busy_slot.id
    await session.commit()
    return user, target_case, target_consultation, target_window, busy_slot


@pytest.mark.asyncio
async def test_booking_service_rejects_overlapping_client_consultation(
    client_schedule_db,
):
    async with client_schedule_db() as session:
        user, case, consultation, window, _ = (
            await seed_client_with_busy_interval(session, suffix=1)
        )

        with pytest.raises(
            ClientConsultationConflictError,
            match="другая активная консультация",
        ):
            await ConsultationBookingService(session).reserve_availability_option(
                consultation=consultation,
                case=case,
                client_id=user.id,
                availability_window_id=window.id,
                starts_at=window.starts_at,
                duration_minutes=45,
            )
        await session.rollback()

        assert (
            await session.scalar(
                select(func.count(ConsultationSlot.id)).where(
                    ConsultationSlot.consultation_id == consultation.id,
                    ConsultationSlot.status == "held",
                )
            )
            == 0
        )


@pytest.mark.asyncio
async def test_reschedule_conflict_preserves_original_booking(client_schedule_db):
    async with client_schedule_db() as session:
        now = datetime.now(timezone.utc)
        user = User(
            telegram_id=1_006_100,
            full_name="Клиент переноса с конфликтом",
        )
        lawyer = Lawyer(
            full_name="Юрист переноса с конфликтом",
            is_active=True,
            workload_limit=10,
        )
        session.add_all([user, lawyer])
        await session.flush()
        old_window = await ConsultationAvailabilityService(session).create_window(
            lawyer_id=lawyer.id,
            starts_at=now + timedelta(days=2),
            ends_at=now + timedelta(days=2, hours=2),
            allowed_durations=(45,),
            actor_type="admin",
            actor_id=None,
            now=now,
        )
        new_window = await ConsultationAvailabilityService(session).create_window(
            lawyer_id=lawyer.id,
            starts_at=now + timedelta(days=4),
            ends_at=now + timedelta(days=4, hours=2),
            allowed_durations=(45,),
            actor_type="admin",
            actor_id=None,
            now=now,
        )
        current_case = Case(
            case_number="RESCHEDULE-CONFLICT-CURRENT",
            client_id=user.id,
            route=RouteCode.M2.value,
            status=CaseStatus.M2_CONSULTATION_BOOKED.value,
            assigned_lawyer_id=lawyer.id,
        )
        other_case = Case(
            case_number="RESCHEDULE-CONFLICT-OTHER",
            client_id=user.id,
            route=RouteCode.M2.value,
            status=CaseStatus.M2_CONSULTATION_BOOKED.value,
        )
        session.add_all([current_case, other_case])
        await session.flush()
        current = Consultation(
            case_id=current_case.id,
            lawyer_id=lawyer.id,
            status=ConsultationStatus.BOOKED.value,
            scheduled_at=old_window.starts_at,
        )
        other = Consultation(
            case_id=other_case.id,
            lawyer_id=lawyer.id,
            status=ConsultationStatus.BOOKED.value,
            scheduled_at=new_window.starts_at,
        )
        session.add_all([current, other])
        await session.flush()
        old_slot = ConsultationSlot(
            lawyer_id=lawyer.id,
            availability_window_id=old_window.id,
            case_id=current_case.id,
            starts_at=old_window.starts_at,
            ends_at=old_window.starts_at + timedelta(minutes=45),
            status="booked",
            held_by_user_id=user.id,
            consultation_id=current.id,
        )
        conflict_slot = ConsultationSlot(
            lawyer_id=lawyer.id,
            availability_window_id=new_window.id,
            case_id=other_case.id,
            starts_at=new_window.starts_at,
            ends_at=new_window.starts_at + timedelta(minutes=45),
            status="booked",
            held_by_user_id=user.id,
            consultation_id=other.id,
        )
        session.add_all([old_slot, conflict_slot])
        await session.flush()
        current.slot_id = old_slot.id
        other.slot_id = conflict_slot.id
        await session.commit()

        with pytest.raises(ConsultationSlotError, match="другая консультация"):
            await ConsultationRescheduleService(session).reschedule(
                consultation=current,
                case=current_case,
                client_id=user.id,
                availability_window_id=new_window.id,
                starts_at=new_window.starts_at,
                duration_minutes=45,
                actor_id=user.id,
            )
        await session.rollback()

        await session.refresh(current)
        await session.refresh(old_slot)
        assert current.slot_id == old_slot.id
        assert old_slot.status == "booked"
        assert old_slot.case_id == current_case.id
        assert (
            await session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_id == current_case.id,
                    AuditLog.action == "CONSULTATION_RESCHEDULED",
                )
            )
            == 0
        )
