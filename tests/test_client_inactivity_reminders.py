from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.notifications.client_inactivity_service import (
    ClientInactivityReminderService,
    _utc,
)
from app.models import Base
from app.models.case import Case
from app.models.notification import Notification
from app.models.user import User


def test_inactivity_reminder_is_once_per_stable_client_stage_snapshot() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
        old = now - timedelta(days=2)
        async with session_factory() as db:
            user = User(
                telegram_id=990000101,
                full_name="Inactivity Test",
                last_activity_at=old,
            )
            db.add(user)
            await db.flush()
            case = Case(
                case_number="INACTIVITY-CASE-1",
                client_id=user.id,
                route=None,
                status="CALCULATED",
                last_client_action_at=old,
                created_at=old,
                updated_at=old,
            )
            db.add(case)
            await db.commit()

            service = ClientInactivityReminderService(db)
            assert await service.run(now=now) == 1
            await db.commit()
            assert await service.run(now=now) == 0
            await db.commit()

            count = await db.scalar(
                select(func.count(Notification.id)).where(
                    Notification.case_id == case.id,
                    Notification.event_code == "CLIENT_INACTIVITY_REMINDER",
                )
            )
            assert count == 1

            # New client activity creates a new stable snapshot and may produce a
            # later reminder after the configured quiet period.
            newer = now + timedelta(hours=1)
            case.last_client_action_at = newer
            case.updated_at = newer
            await db.commit()
            future = newer + timedelta(days=2)
            assert await service.run(now=future) == 1
            await db.commit()

            count = await db.scalar(
                select(func.count(Notification.id)).where(
                    Notification.case_id == case.id,
                    Notification.event_code == "CLIENT_INACTIVITY_REMINDER",
                )
            )
            assert count == 2

        await engine.dispose()

    asyncio.run(scenario())


def test_naive_persisted_activity_is_interpreted_as_utc_not_host_local_time() -> None:
    naive = datetime(2026, 8, 19, 12, 0)
    normalized = _utc(naive)

    assert normalized.tzinfo is timezone.utc
    assert normalized.isoformat() == "2026-08-19T12:00:00+00:00"
