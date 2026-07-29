from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.notifications.notification_sender import NotificationSender
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.admin_user import AdminUser
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.notification import Notification
from app.models.user import User
from app.scheduler.jobs import SchedulerJobs


class FakeBot:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, *, chat_id: int, text: str):
        if self.error:
            raise self.error
        self.messages.append((chat_id, text))


async def create_database(tmp_path, name: str):
    database_path = tmp_path / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, session_factory


async def create_context(session, *, suffix: int = 1):
    user = User(
        telegram_id=910000 + suffix,
        full_name=f"Клиент {suffix}",
    )
    lawyer = Lawyer(
        full_name=f"Юрист {suffix}",
        telegram_id=920000 + suffix,
        is_active=True,
    )
    admin = AdminUser(
        full_name=f"Администратор {suffix}",
        username=f"admin-notify-{suffix}",
        email=f"admin-notify-{suffix}@example.com",
        telegram_id=930000 + suffix,
        password_hash="test-password-hash",
        role="admin",
        is_active=True,
    )
    session.add_all([user, lawyer, admin])
    await session.flush()

    case = Case(
        case_number=f"NOTIFY-{suffix}",
        client_id=user.id,
        assigned_lawyer_id=lawyer.id,
        route="M2",
        status=CaseStatus.M2_CONSULTATION_BOOKED,
        title="Тест уведомлений",
    )
    session.add(case)
    await session.flush()
    return user, lawyer, admin, case


@pytest.mark.asyncio
async def test_engine_routes_and_deduplicates_concrete_recipients(tmp_path):
    engine, session_factory = await create_database(
        tmp_path,
        "notification-routing.db",
    )
    async with session_factory() as session:
        user, lawyer, admin, case = await create_context(session, suffix=1)
        await session.commit()

        created = await NotificationEngine(session).emit(
            event_code="M2_CONSULTATION_BOOKED",
            case_id=case.id,
            payload={"date": "30.07.2026 11:00"},
            dedupe_key=f"case:{case.id}:consultation-booked",
        )
        await session.commit()

        assert len(created) == 3
        assert {item.target_chat_id for item in created} == {
            user.telegram_id,
            lawyer.telegram_id,
            admin.telegram_id,
        }
        assert all(item.status == "PENDING" for item in created)
        assert all(item.dedupe_key for item in created)

        duplicate = await NotificationEngine(session).emit(
            event_code="M2_CONSULTATION_BOOKED",
            case_id=case.id,
            payload={"date": "30.07.2026 11:00"},
            dedupe_key=f"case:{case.id}:consultation-booked",
        )
        await session.commit()

        count = (
            await session.execute(select(func.count(Notification.id)))
        ).scalar_one()
        assert duplicate == []
        assert count == 3

    await engine.dispose()


@pytest.mark.asyncio
async def test_sender_marks_sent_only_after_real_bot_call(tmp_path):
    engine, session_factory = await create_database(
        tmp_path,
        "notification-send.db",
    )
    async with session_factory() as session:
        _, _, _, case = await create_context(session, suffix=2)
        await session.commit()
        await NotificationEngine(session).emit(
            event_code="M2_CONSULTATION_BOOKED",
            case_id=case.id,
            payload={"date": "30.07.2026 11:00"},
            dedupe_key=f"case:{case.id}:send",
        )
        await session.commit()

        bot = FakeBot()
        sent = await NotificationSender(session, bot=bot).send_pending()
        await session.commit()

        notifications = list(
            (
                await session.execute(
                    select(Notification).order_by(Notification.id.asc())
                )
            ).scalars().all()
        )
        assert sent == 3
        assert len(bot.messages) == 3
        assert all(item.status == "SENT" for item in notifications)
        assert all(item.is_sent for item in notifications)
        assert all(item.sent_at is not None for item in notifications)
        assert all(item.attempt_count == 1 for item in notifications)

    await engine.dispose()


@pytest.mark.asyncio
async def test_sender_retries_network_failure_without_false_sent_status(tmp_path):
    engine, session_factory = await create_database(
        tmp_path,
        "notification-retry.db",
    )
    async with session_factory() as session:
        user, _, _, case = await create_context(session, suffix=3)
        notification = Notification(
            case_id=case.id,
            user_id=user.id,
            channel="telegram",
            event_code="TEST_RETRY",
            recipient_type="client",
            target_chat_id=user.telegram_id,
            title="client",
            text="Проверка повторной отправки",
            status="PENDING",
            is_sent=False,
        )
        session.add(notification)
        await session.commit()

        sent = await NotificationSender(
            session,
            bot=FakeBot(error=OSError("temporary network failure")),
        ).send_pending()
        await session.commit()
        await session.refresh(notification)

        assert sent == 0
        assert notification.status == "RETRY"
        assert notification.is_sent is False
        assert notification.sent_at is None
        assert notification.attempt_count == 1
        assert "temporary network failure" in notification.last_error
        assert notification.next_attempt_at is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_consultation_reminders_are_created_once_per_window(tmp_path):
    engine, session_factory = await create_database(
        tmp_path,
        "consultation-reminders.db",
    )
    async with session_factory() as session:
        user, lawyer, _, case = await create_context(session, suffix=4)
        starts_at = datetime.now(timezone.utc) + timedelta(minutes=90)
        slot = ConsultationSlot(
            lawyer_id=lawyer.id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            status="booked",
            held_by_user_id=user.id,
        )
        session.add(slot)
        await session.flush()
        consultation = Consultation(
            case_id=case.id,
            lawyer_id=lawyer.id,
            slot_id=slot.id,
            status=ConsultationStatus.BOOKED,
            scheduled_at=starts_at,
        )
        session.add(consultation)
        await session.flush()
        slot.consultation_id = consultation.id
        await session.commit()

        first = await SchedulerJobs(session).check_consultation_reminders()
        await session.commit()
        second = await SchedulerJobs(session).check_consultation_reminders()
        await session.commit()

        count = (
            await session.execute(
                select(func.count(Notification.id)).where(
                    Notification.event_code == "CONSULTATION_REMINDER_2H"
                )
            )
        ).scalar_one()
        assert first == {"within_24h": 0, "within_2h": 1}
        assert second == {"within_24h": 0, "within_2h": 0}
        assert count == 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_overdue_consultation_creates_one_control_alert(tmp_path):
    engine, session_factory = await create_database(
        tmp_path,
        "consultation-overdue.db",
    )
    async with session_factory() as session:
        user, lawyer, _, case = await create_context(session, suffix=5)
        starts_at = datetime.now(timezone.utc) - timedelta(hours=2)
        slot = ConsultationSlot(
            lawyer_id=lawyer.id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            status="booked",
            held_by_user_id=user.id,
        )
        session.add(slot)
        await session.flush()
        consultation = Consultation(
            case_id=case.id,
            lawyer_id=lawyer.id,
            slot_id=slot.id,
            status=ConsultationStatus.BOOKED,
            scheduled_at=starts_at,
        )
        session.add(consultation)
        await session.flush()
        slot.consultation_id = consultation.id
        await session.commit()

        first = await SchedulerJobs(
            session
        ).check_consultation_completion_overdue()
        await session.commit()
        second = await SchedulerJobs(
            session
        ).check_consultation_completion_overdue()
        await session.commit()

        count = (
            await session.execute(
                select(func.count(Notification.id)).where(
                    Notification.event_code
                    == "CONSULTATION_COMPLETION_OVERDUE"
                )
            )
        ).scalar_one()
        assert first == 1
        assert second == 0
        assert count == 2

    await engine.dispose()
