from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.availability_service import (
    ConsultationAvailabilityService,
)
from app.domain.consultations.consultation_service import ConsultationSlotError
from app.domain.consultations.reschedule_service import (
    ConsultationRescheduleService,
)
from app.domain.consultations.slot_selection_service import (
    ConsultationSlotSelectionError,
    ConsultationSlotSelectionService,
)
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.payment import Payment
from app.models.user import User


@pytest.fixture
async def duration_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'reschedule-duration.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def seed_duration_case(session):
    now = datetime.now(timezone.utc)
    user = User(telegram_id=1_007_001, full_name="Клиент длительности")
    lawyer = Lawyer(
        full_name="Юрист длительности",
        is_active=True,
        workload_limit=10,
    )
    session.add_all([user, lawyer])
    await session.flush()
    case = Case(
        case_number="RESCHEDULE-DURATION",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_CONSULTATION_BOOKED.value,
        assigned_lawyer_id=lawyer.id,
    )
    session.add(case)
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
        starts_at=now + timedelta(days=3),
        ends_at=now + timedelta(days=3, hours=3),
        allowed_durations=(30, 45, 60),
        actor_type="admin",
        actor_id=None,
        now=now,
    )
    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        status=ConsultationStatus.BOOKED.value,
        scheduled_at=old_window.starts_at,
    )
    session.add(consultation)
    await session.flush()
    old_slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        availability_window_id=old_window.id,
        case_id=case.id,
        starts_at=old_window.starts_at,
        ends_at=old_window.starts_at + timedelta(minutes=45),
        duration_minutes=45,
        status="booked",
        held_by_user_id=user.id,
        consultation_id=consultation.id,
    )
    session.add(old_slot)
    await session.flush()
    consultation.slot_id = old_slot.id
    payment = Payment(
        case_id=case.id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT.value,
        title="Юридическая консультация 45 минут",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=PaymentStatus.PAID.value,
        provider="fake",
        provider_payment_id="duration-paid-1",
    )
    session.add(payment)
    await session.commit()
    return user, case, consultation, old_slot, new_window, payment


@pytest.mark.asyncio
async def test_reschedule_selection_keeps_paid_duration(duration_db):
    async with duration_db() as session:
        user, case, consultation, _, new_window, _ = (
            await seed_duration_case(session)
        )

        selection = await ConsultationSlotSelectionService(
            session
        ).build_selection(
            client_id=user.id,
            case_id=case.id,
            consultation_id=consultation.id,
            reschedule=True,
            horizon_days=30,
        )

        assert selection.slots
        assert selection.durations == (45,)
        assert {item.duration_minutes for item in selection.slots} == {45}
        assert any(
            item.availability_window_reference == new_window.id
            for item in selection.slots
        )
        with pytest.raises(
            ConsultationSlotSelectionError,
            match="отдельного перерасчёта",
        ):
            await ConsultationSlotSelectionService(session).build_selection(
                client_id=user.id,
                case_id=case.id,
                consultation_id=consultation.id,
                duration_minutes=60,
                reschedule=True,
                horizon_days=30,
            )


@pytest.mark.asyncio
async def test_direct_duration_change_preserves_booking_and_payment(duration_db):
    async with duration_db() as session:
        user, case, consultation, old_slot, new_window, payment = (
            await seed_duration_case(session)
        )

        with pytest.raises(
            ConsultationSlotError,
            match="отдельного перерасчёта",
        ):
            await ConsultationRescheduleService(session).reschedule(
                consultation=consultation,
                case=case,
                client_id=user.id,
                availability_window_id=new_window.id,
                starts_at=new_window.starts_at,
                duration_minutes=60,
                actor_id=user.id,
            )
        await session.rollback()

        await session.refresh(consultation)
        await session.refresh(old_slot)
        await session.refresh(payment)
        assert consultation.slot_id == old_slot.id
        assert consultation.scheduled_at == old_slot.starts_at
        assert old_slot.status == "booked"
        assert old_slot.case_id == case.id
        assert payment.status == PaymentStatus.PAID.value
        assert payment.amount == Decimal("5000.00")
        assert (
            await session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_id == case.id,
                    AuditLog.action == "CONSULTATION_RESCHEDULED",
                )
            )
            == 0
        )
