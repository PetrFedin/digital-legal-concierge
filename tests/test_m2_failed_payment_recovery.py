from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.user import User


@pytest.fixture
async def failed_payment_db(tmp_path):
    database_path = tmp_path / "m2-failed-payment-recovery.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def seed_payment(session, *, suffix: int, status: str):
    user = User(
        telegram_id=997_000 + suffix,
        telegram_username=f"failed_payment_{suffix}",
        full_name=f"Клиент {suffix}",
    )
    session.add(user)
    await session.flush()
    case = Case(
        case_number=f"FAILED-M2-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_PAYMENT_PENDING.value,
        title="Юридическая консультация",
        next_action="Оплатите консультацию",
    )
    session.add(case)
    await session.flush()
    payment = Payment(
        case_id=case.id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT.value,
        title="Оплата консультации",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=status,
        provider="fake",
        provider_payment_id=f"failed-provider-{suffix}",
        payment_url=f"https://pay.example.test/{suffix}",
    )
    session.add(payment)
    await session.flush()
    return user, case, payment


async def count_audit(session, *, case_id: int) -> int:
    return int(
        (
            await session.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id == case_id,
                    AuditLog.action == "PAYMENT_FAILED",
                )
            )
        ).scalar_one()
    )


async def count_notifications(session, *, case_id: int) -> int:
    return int(
        (
            await session.execute(
                select(func.count(Notification.id)).where(
                    Notification.case_id == case_id,
                    Notification.event_code == "PAYMENT_FAILED",
                )
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_failed_m2_payment_updates_recovery_state_once(failed_payment_db):
    async with failed_payment_db() as session:
        user, case, payment = await seed_payment(
            session,
            suffix=1,
            status=PaymentStatus.WAITING_CONFIRMATION.value,
        )
        service = PaymentWebhookService(session)

        failed = await service.process_failed_payment(
            payment=payment,
            case=case,
            provider_payload={"event": "payment.failed"},
        )
        await session.commit()

        assert failed.status == PaymentStatus.FAILED.value
        assert failed.payment_url is None
        assert failed.provider == "fake"
        assert failed.provider_payment_id == "failed-provider-1"
        assert case.next_action == (
            "Повторите оплату консультации или выберите другое время"
        )
        assert await count_audit(session, case_id=case.id) == 1
        assert await count_notifications(session, case_id=case.id) == 1
        notification = (
            await session.execute(
                select(Notification).where(
                    Notification.case_id == case.id,
                    Notification.event_code == "PAYMENT_FAILED",
                    Notification.user_id == user.id,
                )
            )
        ).scalar_one()
        assert case.case_number in notification.text
        assert notification.title == "client"

        await service.process_failed_payment(
            payment=payment,
            case=case,
            provider_payload={"event": "payment.failed.retry"},
        )
        await session.commit()

        assert await count_audit(session, case_id=case.id) == 1
        assert await count_notifications(session, case_id=case.id) == 1


@pytest.mark.asyncio
async def test_delayed_failure_does_not_override_paid_payment(failed_payment_db):
    async with failed_payment_db() as session:
        _, case, payment = await seed_payment(
            session,
            suffix=2,
            status=PaymentStatus.PAID.value,
        )
        original_url = payment.payment_url

        result = await PaymentWebhookService(session).process_failed_payment(
            payment=payment,
            case=case,
            provider_payload={"event": "payment.failed.delayed"},
        )

        assert result.status == PaymentStatus.PAID.value
        assert result.payment_url == original_url
        assert case.next_action == "Оплатите консультацию"
        assert await count_audit(session, case_id=case.id) == 0
        assert await count_notifications(session, case_id=case.id) == 0
