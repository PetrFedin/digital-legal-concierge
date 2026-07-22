from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.payment_lifecycle_service import (
    ConsultationPaymentLifecycleService,
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


async def _create_test_database(tmp_path, name: str):
    database_path = tmp_path / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, session_factory


async def _seed_paid_consultation(session, suffix: str):
    starts_at = datetime.now(timezone.utc) + timedelta(days=2)
    user = User(
        telegram_id=920_000 + int(suffix),
        telegram_username=f"notify_client_{suffix}",
        full_name=f"Клиент уведомления {suffix}",
    )
    lawyer = Lawyer(
        full_name=f"Юрист уведомления {suffix}",
        email=f"notify-lawyer-{suffix}@example.test",
        is_active=True,
    )
    session.add_all([user, lawyer])
    await session.flush()

    case = Case(
        case_number=f"TEST-M2-NOTIFY-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_PAYMENT_PENDING.value,
        title="Проверка уведомления о консультации",
        next_action="Ожидайте подтверждения консультации",
    )
    session.add(case)
    await session.flush()

    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        status=ConsultationStatus.PAID_PENDING_CONFIRMATION.value,
        scheduled_at=starts_at,
        client_description="Нужна консультация.",
    )
    session.add(consultation)
    await session.flush()

    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        status="booked",
        hold_expires_at=None,
        held_by_user_id=user.id,
        consultation_id=consultation.id,
    )
    session.add(slot)
    await session.flush()
    consultation.slot_id = slot.id
    await session.commit()

    return {
        "user_id": user.id,
        "lawyer_id": lawyer.id,
        "case_id": case.id,
        "consultation_id": consultation.id,
        "starts_at": starts_at,
    }


async def _client_notifications(session, *, case_id: int, user_id: int):
    return list(
        (
            await session.execute(
                select(Notification)
                .where(
                    Notification.case_id == case_id,
                    Notification.user_id == user_id,
                    Notification.event_code
                    == ConsultationPaymentLifecycleService.CLIENT_BOOKED_EVENT,
                )
                .order_by(Notification.id.asc())
            )
        )
        .scalars()
        .all()
    )


@pytest.mark.asyncio
async def test_lawyer_confirmation_creates_one_client_notification(tmp_path):
    engine, session_factory = await _create_test_database(
        tmp_path,
        "m2-confirmation-notification.db",
    )
    async with session_factory() as session:
        ids = await _seed_paid_consultation(session, "101")

    async with session_factory() as session:
        case = await session.get(Case, ids["case_id"])
        lifecycle = ConsultationPaymentLifecycleService(session)

        await lifecycle.confirm_by_lawyer(
            case=case,
            lawyer_id=ids["lawyer_id"],
            source="test",
        )
        await session.commit()

        notifications = await _client_notifications(
            session,
            case_id=ids["case_id"],
            user_id=ids["user_id"],
        )
        assert len(notifications) == 1
        notification = notifications[0]
        assert notification.channel == "telegram"
        assert notification.status == "PENDING"
        assert notification.is_sent is False
        assert notification.title == "Консультация подтверждена"
        assert "Юрист уведомления 101" in notification.text
        assert "TEST-M2-NOTIFY-101" in notification.text
        assert ids["starts_at"].isoformat() in notification.text

        await lifecycle.confirm_by_lawyer(
            case=case,
            lawyer_id=ids["lawyer_id"],
            source="test-retry",
        )
        await session.commit()

        assert len(
            await _client_notifications(
                session,
                case_id=ids["case_id"],
                user_id=ids["user_id"],
            )
        ) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_booked_consultation_backfills_missing_notification(tmp_path):
    engine, session_factory = await _create_test_database(
        tmp_path,
        "m2-confirmation-notification-backfill.db",
    )
    async with session_factory() as session:
        ids = await _seed_paid_consultation(session, "102")
        case = await session.get(Case, ids["case_id"])
        consultation = await session.get(Consultation, ids["consultation_id"])
        consultation.status = ConsultationStatus.BOOKED.value
        case.status = CaseStatus.M2_CONSULTATION_BOOKED.value
        case.assigned_lawyer_id = ids["lawyer_id"]
        case.next_action = "Ожидайте консультации в выбранное время"
        await session.commit()

    async with session_factory() as session:
        case = await session.get(Case, ids["case_id"])
        await ConsultationPaymentLifecycleService(session).confirm_by_lawyer(
            case=case,
            lawyer_id=ids["lawyer_id"],
            source="test-backfill",
        )
        await session.commit()

        notifications = await _client_notifications(
            session,
            case_id=ids["case_id"],
            user_id=ids["user_id"],
        )
        assert len(notifications) == 1

        count = (
            await session.execute(
                select(func.count(Notification.id)).where(
                    Notification.case_id == ids["case_id"],
                    Notification.event_code
                    == ConsultationPaymentLifecycleService.CLIENT_BOOKED_EVENT,
                )
            )
        ).scalar_one()
        assert count == 1

    await engine.dispose()
