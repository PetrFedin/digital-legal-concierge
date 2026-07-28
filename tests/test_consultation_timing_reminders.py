from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.models import Base
from app.models.case import Case
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.notification import Notification
from app.models.user import User
from app.scheduler.jobs import SchedulerJobs


@pytest.fixture
async def reminder_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'consultation-reminders.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def create_case(session, *, suffix: int):
    user = User(
        telegram_id=999_950 + suffix,
        full_name=f"Клиент напоминаний {suffix}",
    )
    lawyer = Lawyer(
        full_name=f"Юрист напоминаний {suffix}",
        is_active=True,
    )
    session.add_all([user, lawyer])
    await session.flush()
    case = Case(
        case_number=f"REMINDER-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_CONSULTATION_BOOKED.value,
        title="Юридическая консультация",
        assigned_lawyer_id=lawyer.id,
    )
    session.add(case)
    await session.flush()
    return user, lawyer, case


async def count_event(session, event_code: str) -> int:
    return int(
        await session.scalar(
            select(func.count(Notification.id)).where(
                Notification.event_code == event_code
            )
        )
        or 0
    )


@pytest.mark.asyncio
async def test_hold_expiry_reminder_is_scoped_and_idempotent(reminder_db):
    async with reminder_db() as session:
        now = datetime.now(timezone.utc)
        user, lawyer, case = await create_case(session, suffix=1)
        soon = ConsultationSlot(
            lawyer_id=lawyer.id,
            case_id=case.id,
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=1, minutes=45),
            status="held",
            hold_expires_at=now + timedelta(minutes=4),
            held_by_user_id=user.id,
        )
        later = ConsultationSlot(
            lawyer_id=lawyer.id,
            case_id=case.id,
            starts_at=now + timedelta(days=2),
            ends_at=now + timedelta(days=2, minutes=45),
            status="held",
            hold_expires_at=now + timedelta(minutes=10),
            held_by_user_id=user.id,
        )
        session.add_all([soon, later])
        await session.commit()

        first = await SchedulerJobs(session).check_expiring_consultation_holds()
        second = await SchedulerJobs(session).check_expiring_consultation_holds()
        await session.commit()

        assert first == second == 1
        assert await count_event(session, "CONSULTATION_HOLD_EXPIRING") == 1
        notification = (
            await session.execute(
                select(Notification).where(
                    Notification.event_code == "CONSULTATION_HOLD_EXPIRING"
                )
            )
        ).scalar_one()
        assert notification.user_id == user.id
        assert "Резерв времени консультации закончится" in notification.text
        assert "МСК" in notification.text


@pytest.mark.asyncio
async def test_consultation_reminder_windows_do_not_overlap(reminder_db):
    async with reminder_db() as session:
        now = datetime.now(timezone.utc)
        user, lawyer, case = await create_case(session, suffix=2)
        in_two_hours = ConsultationSlot(
            lawyer_id=lawyer.id,
            case_id=case.id,
            starts_at=now + timedelta(hours=2),
            ends_at=now + timedelta(hours=2, minutes=45),
            status="booked",
            held_by_user_id=user.id,
        )
        in_thirty_minutes = ConsultationSlot(
            lawyer_id=lawyer.id,
            case_id=case.id,
            starts_at=now + timedelta(minutes=30),
            ends_at=now + timedelta(minutes=75),
            status="booked",
            held_by_user_id=user.id,
        )
        after_two_days = ConsultationSlot(
            lawyer_id=lawyer.id,
            case_id=case.id,
            starts_at=now + timedelta(days=2),
            ends_at=now + timedelta(days=2, minutes=45),
            status="booked",
            held_by_user_id=user.id,
        )
        session.add_all([in_two_hours, in_thirty_minutes, after_two_days])
        await session.commit()

        first = await SchedulerJobs(
            session
        ).check_consultation_schedule_reminders()
        second = await SchedulerJobs(
            session
        ).check_consultation_schedule_reminders()
        await session.commit()

        assert first == second == {"24h": 1, "1h": 1}
        assert await count_event(session, "CONSULTATION_REMINDER_24H") == 1
        assert await count_event(session, "CONSULTATION_REMINDER_1H") == 1

        notifications = list(
            (
                await session.execute(
                    select(Notification).where(
                        Notification.event_code.in_(
                            {
                                "CONSULTATION_REMINDER_24H",
                                "CONSULTATION_REMINDER_1H",
                            }
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {item.user_id for item in notifications} == {user.id}
        assert all("МСК" in item.text for item in notifications)
        assert all(item.title == "client" for item in notifications)
