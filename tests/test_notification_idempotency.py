from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.consultation_service import ConsultationSlotError
from app.domain.consultations.reschedule_service import (
    ConsultationRescheduleService,
)
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.user import User
from app.scheduler.jobs import SchedulerJobs


@pytest.fixture
async def notification_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'notification-idempotency.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def notification_count(
    session,
    *,
    event_code: str,
    case_id: int | None = None,
) -> int:
    query = select(func.count(Notification.id)).where(
        Notification.event_code == event_code
    )
    if case_id is not None:
        query = query.where(Notification.case_id == case_id)
    return int(await session.scalar(query) or 0)


async def create_user_and_case(session, *, suffix: int = 1):
    user = User(
        telegram_id=999_100 + suffix,
        full_name=f"Клиент уведомлений {suffix}",
    )
    session.add(user)
    await session.flush()
    case = Case(
        case_number=f"NOTIFICATION-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_CONSULTATION_BOOKED.value,
        title="Юридическая консультация",
    )
    session.add(case)
    await session.flush()
    return user, case


@pytest.mark.asyncio
async def test_same_rendered_event_is_reused(notification_db):
    async with notification_db() as session:
        user, case = await create_user_and_case(session)
        engine = NotificationEngine(session)

        first = await engine.emit(
            event_code="CONSULTATION_RESCHEDULED",
            case_id=case.id,
            user_id=user.id,
            payload={"date": "2026-08-10T12:00:00+00:00"},
        )
        second = await engine.emit(
            event_code="CONSULTATION_RESCHEDULED",
            case_id=case.id,
            user_id=user.id,
            payload={"date": "2026-08-10T12:00:00+00:00"},
        )
        await session.commit()

        assert first[0].id == second[0].id
        assert await notification_count(
            session,
            event_code="CONSULTATION_RESCHEDULED",
            case_id=case.id,
        ) == 1


@pytest.mark.asyncio
async def test_changed_rendered_event_creates_new_notification(notification_db):
    async with notification_db() as session:
        user, case = await create_user_and_case(session, suffix=2)
        engine = NotificationEngine(session)

        await engine.emit(
            event_code="CONSULTATION_RESCHEDULED",
            case_id=case.id,
            user_id=user.id,
            payload={"date": "2026-08-10T12:00:00+00:00"},
        )
        await engine.emit(
            event_code="CONSULTATION_RESCHEDULED",
            case_id=case.id,
            user_id=user.id,
            payload={"date": "2026-08-11T12:00:00+00:00"},
        )
        await session.commit()

        assert await notification_count(
            session,
            event_code="CONSULTATION_RESCHEDULED",
            case_id=case.id,
        ) == 2


@pytest.mark.asyncio
async def test_scheduler_payment_reminder_is_idempotent(notification_db):
    async with notification_db() as session:
        user, case = await create_user_and_case(session, suffix=3)
        case.status = CaseStatus.M2_PAYMENT_PENDING.value
        session.add(
            Payment(
                case_id=case.id,
                payment_code=PaymentCode.M2_CONSULTATION_PAYMENT.value,
                title="Юридическая консультация",
                amount=Decimal("5000.00"),
                currency="RUB",
                status=PaymentStatus.PENDING.value,
            )
        )
        await session.commit()

        first = await SchedulerJobs(session).check_unpaid_payments()
        second = await SchedulerJobs(session).check_unpaid_payments()
        await session.commit()

        assert first == second == 1
        assert await notification_count(
            session,
            event_code="PAYMENT_REMINDER",
            case_id=case.id,
        ) == 1
        notification = (
            await session.execute(
                select(Notification).where(
                    Notification.event_code == "PAYMENT_REMINDER",
                    Notification.case_id == case.id,
                )
            )
        ).scalar_one()
        assert notification.user_id == user.id


async def seed_reschedule_context(session, *, suffix: int):
    user, case = await create_user_and_case(session, suffix=suffix)
    lawyer = Lawyer(
        full_name=f"Юрист уведомлений {suffix}",
        is_active=True,
    )
    session.add(lawyer)
    await session.flush()
    case.assigned_lawyer_id = lawyer.id

    starts_at = datetime.now(timezone.utc) + timedelta(days=2)
    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        status=ConsultationStatus.BOOKED.value,
        scheduled_at=starts_at,
    )
    session.add(consultation)
    await session.flush()
    old_slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        status="booked",
        held_by_user_id=user.id,
        consultation_id=consultation.id,
    )
    replacement = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at + timedelta(days=1),
        ends_at=starts_at + timedelta(days=1, hours=1),
        status="available",
    )
    session.add_all([old_slot, replacement])
    await session.flush()
    consultation.slot_id = old_slot.id
    await session.commit()
    return user, case, consultation, old_slot, replacement


@pytest.mark.asyncio
async def test_successful_reschedule_notifies_once(notification_db):
    async with notification_db() as session:
        user, case, consultation, _, replacement = await seed_reschedule_context(
            session,
            suffix=4,
        )
        service = ConsultationRescheduleService(session)

        await service.reschedule(
            consultation=consultation,
            case=case,
            client_id=user.id,
            new_slot_id=replacement.id,
            actor_id=user.id,
        )
        await service.reschedule(
            consultation=consultation,
            case=case,
            client_id=user.id,
            new_slot_id=replacement.id,
            actor_id=user.id,
        )
        await session.commit()

        assert await notification_count(
            session,
            event_code="CONSULTATION_RESCHEDULED",
            case_id=case.id,
        ) == 1


@pytest.mark.asyncio
async def test_failed_reschedule_creates_no_notification(notification_db):
    async with notification_db() as session:
        user, case, consultation, old_slot, replacement = (
            await seed_reschedule_context(session, suffix=5)
        )
        replacement.status = "booked"
        await session.commit()

        with pytest.raises(ConsultationSlotError):
            await ConsultationRescheduleService(session).reschedule(
                consultation=consultation,
                case=case,
                client_id=user.id,
                new_slot_id=replacement.id,
                actor_id=user.id,
            )
        await session.rollback()
        await session.refresh(consultation)
        await session.refresh(old_slot)

        assert consultation.slot_id == old_slot.id
        assert old_slot.status == "booked"
        assert await notification_count(
            session,
            event_code="CONSULTATION_RESCHEDULED",
            case_id=case.id,
        ) == 0
