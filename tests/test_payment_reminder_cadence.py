from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.case import Case
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.user import User
from app.scheduler.jobs import SchedulerJobs


async def create_database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, session_factory


async def create_payment(
    session,
    *,
    suffix: int,
    code: str,
    age_hours: int,
) -> tuple[Case, Payment]:
    user = User(
        telegram_id=995000000 + suffix,
        full_name=f"Reminder Client {suffix}",
    )
    session.add(user)
    await session.flush()

    case = Case(
        case_number=f"PAY-REM-{suffix}",
        client_id=user.id,
        route="M2" if code == PaymentCode.M2_CONSULTATION_PAYMENT else "M1",
        status=(
            "M2_PAYMENT_PENDING"
            if code == PaymentCode.M2_CONSULTATION_PAYMENT
            else "M1_PAYMENT_30000_PENDING"
        ),
        title="Payment reminder cadence",
    )
    session.add(case)
    await session.flush()

    now = datetime.now(timezone.utc)
    payment = Payment(
        case_id=case.id,
        payment_code=code,
        title="Payment reminder test",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=PaymentStatus.WAITING_CONFIRMATION,
        created_at=now - timedelta(hours=age_hours),
        updated_at=now - timedelta(hours=age_hours),
    )
    session.add(payment)
    await session.flush()
    return case, payment


@pytest.mark.asyncio
async def test_m1_payment_reminders_follow_24h_72h_7d_cadence():
    engine, session_factory = await create_database()
    async with session_factory() as session:
        case, payment = await create_payment(
            session,
            suffix=1,
            code=PaymentCode.M1_INITIAL_PAYMENT,
            age_hours=25,
        )
        await session.commit()

        jobs = SchedulerJobs(session)
        assert await jobs.check_unpaid_payments() == 1
        await session.commit()
        assert await jobs.check_unpaid_payments() == 0
        await session.commit()

        payment.created_at = datetime.now(timezone.utc) - timedelta(hours=73)
        await session.commit()
        assert await jobs.check_unpaid_payments() == 1
        await session.commit()

        payment.created_at = datetime.now(timezone.utc) - timedelta(hours=169)
        await session.commit()
        assert await jobs.check_unpaid_payments() == 1
        await session.commit()

        rows = (
            await session.execute(
                select(Notification)
                .where(
                    Notification.case_id == case.id,
                    Notification.event_code == "PAYMENT_REMINDER",
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


@pytest.mark.asyncio
async def test_consultation_payment_reminders_follow_1h_12h_24h_cadence():
    engine, session_factory = await create_database()
    async with session_factory() as session:
        case, payment = await create_payment(
            session,
            suffix=2,
            code=PaymentCode.M2_CONSULTATION_PAYMENT,
            age_hours=2,
        )
        await session.commit()

        jobs = SchedulerJobs(session)
        assert await jobs.check_unpaid_payments() == 1
        await session.commit()

        payment.created_at = datetime.now(timezone.utc) - timedelta(hours=13)
        await session.commit()
        assert await jobs.check_unpaid_payments() == 1
        await session.commit()

        payment.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
        await session.commit()
        assert await jobs.check_unpaid_payments() == 1
        await session.commit()
        assert await jobs.check_unpaid_payments() == 0
        await session.commit()

        count = (
            await session.execute(
                select(func.count(Notification.id)).where(
                    Notification.case_id == case.id,
                    Notification.event_code == "PAYMENT_REMINDER",
                )
            )
        ).scalar_one()
        assert count == 3

        rows = (
            await session.execute(
                select(Notification).where(
                    Notification.case_id == case.id,
                    Notification.event_code == "PAYMENT_REMINDER",
                )
            )
        ).scalars().all()
        keys = [row.dedupe_key or "" for row in rows]
        assert any(":1h:" in key for key in keys)
        assert any(":12h:" in key for key in keys)
        assert any(":24h:" in key for key in keys)

    await engine.dispose()


@pytest.mark.asyncio
async def test_payment_reminder_recovery_sends_only_latest_due_threshold():
    engine, session_factory = await create_database()
    async with session_factory() as session:
        case, _payment = await create_payment(
            session,
            suffix=3,
            code=PaymentCode.M1_INITIAL_PAYMENT,
            age_hours=200,
        )
        await session.commit()

        assert await SchedulerJobs(session).check_unpaid_payments() == 1
        await session.commit()

        rows = (
            await session.execute(
                select(Notification).where(
                    Notification.case_id == case.id,
                    Notification.event_code == "PAYMENT_REMINDER",
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert ":168h:" in (rows[0].dedupe_key or "")

    await engine.dispose()
