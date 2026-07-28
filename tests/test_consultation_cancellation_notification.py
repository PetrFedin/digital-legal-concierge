from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.consultation_service import (
    ConsultationService,
    ConsultationSlotError,
)
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.notification import Notification
from app.models.user import User


@pytest.fixture
async def cancellation_notification_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'cancel-notification.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def seed(session, *, suffix: int):
    user = User(
        telegram_id=999_700 + suffix,
        full_name=f"Клиент отмены {suffix}",
    )
    lawyer = Lawyer(
        full_name=f"Юрист отмены {suffix}",
        is_active=True,
    )
    session.add_all([user, lawyer])
    await session.flush()
    case = Case(
        case_number=f"CANCEL-NOTIFY-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_CONSULTATION_BOOKED.value,
        assigned_lawyer_id=lawyer.id,
    )
    session.add(case)
    await session.flush()
    starts_at = datetime.now(timezone.utc) + timedelta(days=2)
    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        status=ConsultationStatus.BOOKED.value,
        scheduled_at=starts_at,
    )
    session.add(consultation)
    await session.flush()
    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        status="booked",
        held_by_user_id=user.id,
        consultation_id=consultation.id,
    )
    session.add(slot)
    await session.flush()
    consultation.slot_id = slot.id
    await session.commit()
    return user, case, consultation, slot


async def count_notifications(session, case_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count(Notification.id)).where(
                Notification.case_id == case_id,
                Notification.event_code == "CONSULTATION_CANCELLED",
                Notification.title == "client",
            )
        )
        or 0
    )


@pytest.mark.asyncio
async def test_cancel_notification_is_created_once(
    cancellation_notification_db,
):
    async with cancellation_notification_db() as session:
        user, case, consultation, _ = await seed(session, suffix=1)
        service = ConsultationService(session)

        await service.cancel(
            consultation=consultation,
            case=case,
            actor_type="client",
            actor_id=user.id,
            comment="Клиент подтвердил отмену",
        )
        await service.cancel(
            consultation=consultation,
            case=case,
            actor_type="client",
            actor_id=user.id,
            comment="Повторная отмена",
        )
        await session.commit()

        assert await count_notifications(session, case.id) == 1
        notification = (
            await session.execute(
                select(Notification).where(
                    Notification.case_id == case.id,
                    Notification.event_code == "CONSULTATION_CANCELLED",
                )
            )
        ).scalar_one()
        assert notification.user_id == user.id
        assert notification.status == "PENDING"
        assert "Консультация отменена" in notification.text


@pytest.mark.asyncio
async def test_failed_cancel_creates_no_notification(
    cancellation_notification_db,
):
    async with cancellation_notification_db() as session:
        user, case, consultation, slot = await seed(session, suffix=2)
        conflicting = Consultation(
            case_id=case.id,
            lawyer_id=consultation.lawyer_id,
            status=ConsultationStatus.BOOKED.value,
            scheduled_at=consultation.scheduled_at,
        )
        session.add(conflicting)
        await session.flush()
        slot.consultation_id = conflicting.id
        await session.commit()

        with pytest.raises(ConsultationSlotError):
            await ConsultationService(session).cancel(
                consultation=consultation,
                case=case,
                actor_type="client",
                actor_id=user.id,
                comment="Недопустимая отмена",
            )
        await session.rollback()

        assert await count_notifications(session, case.id) == 0
