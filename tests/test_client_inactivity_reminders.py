from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.notifications.client_inactivity_service import (
    ClientInactivityReminderService,
)
from app.models import Base
from app.models.case import Case
from app.models.notification import Notification
from app.models.user import User


def test_inactivity_reminder_is_deduplicated_per_stable_case_snapshot() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
        old = now - timedelta(days=2)

        async with session_factory() as db:
            user = User(
                telegram_id=990000101,
                full_name="Inactivity Test",
            )
            db.add(user)
            await db.flush()
            case = Case(
                case_number="INACTIVITY-CASE-1",
                client_id=user.id,
                route=None,
                status="CALCULATED",
                next_action="Выбрать дальнейший маршрут",
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

            # A later stable Case snapshot begins a new reminder cycle.
            newer = now + timedelta(hours=1)
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


def test_inactivity_service_sends_only_latest_due_reminder_after_long_outage() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
        old = now - timedelta(days=8)

        async with session_factory() as db:
            user = User(telegram_id=990000102, full_name="Reminder Catchup")
            db.add(user)
            await db.flush()
            case = Case(
                case_number="INACTIVITY-CASE-2",
                client_id=user.id,
                route="M1",
                status="M1_DOCUMENTS_PENDING",
                next_action="Загрузить документы",
                created_at=old,
                updated_at=old,
            )
            db.add(case)
            await db.commit()

            service = ClientInactivityReminderService(db)
            assert await service.run(now=now) == 1
            await db.commit()

            rows = (
                await db.execute(
                    select(Notification).where(
                        Notification.case_id == case.id,
                        Notification.event_code == "CLIENT_INACTIVITY_REMINDER",
                    )
                )
            ).scalars().all()
            assert len(rows) == 1
            assert ":168h:" in (rows[0].dedupe_key or "") or (
                rows[0].dedupe_key or ""
            ).endswith(":168h:client:990000102")

        await engine.dispose()

    asyncio.run(scenario())


def test_inactivity_reminder_follows_24h_72h_7d_series() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        anchor = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)

        async with session_factory() as db:
            user = User(telegram_id=990000103, full_name="Reminder Series")
            db.add(user)
            await db.flush()
            case = Case(
                case_number="INACTIVITY-CASE-3",
                client_id=user.id,
                route="M1",
                status="M1_DOCUMENTS_PENDING",
                next_action="Загрузить документы",
                created_at=anchor,
                updated_at=anchor,
            )
            db.add(case)
            await db.commit()

            service = ClientInactivityReminderService(db)

            assert await service.run(now=anchor + timedelta(hours=25)) == 1
            await db.commit()
            assert await service.run(now=anchor + timedelta(hours=73)) == 1
            await db.commit()
            assert await service.run(now=anchor + timedelta(hours=169)) == 1
            await db.commit()
            assert await service.run(now=anchor + timedelta(hours=170)) == 0
            await db.commit()

            rows = (
                await db.execute(
                    select(Notification)
                    .where(
                        Notification.case_id == case.id,
                        Notification.event_code == "CLIENT_INACTIVITY_REMINDER",
                    )
                    .order_by(Notification.id.asc())
                )
            ).scalars().all()
            assert len(rows) == 3
            keys = [row.dedupe_key or "" for row in rows]
            assert any(":24h:" in key for key in keys)
            assert any(":72h:" in key for key in keys)
            assert any(":168h:" in key for key in keys)

        await engine.dispose()

    asyncio.run(scenario())
