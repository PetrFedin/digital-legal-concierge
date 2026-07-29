from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.payments.payment_processing_outcomes import PaymentProcessingOutcome
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.payment import Payment
from app.models.user import User


@pytest.fixture
async def duplicate_webhook_db(tmp_path):
    database_path = tmp_path / "duplicate-paid-webhook-conflict.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def seed_case(session):
    user = User(
        telegram_id=1_070_001,
        telegram_username="duplicate_paid_webhook",
        full_name="Клиент duplicate webhook",
    )
    session.add(user)
    await session.flush()
    case = Case(
        case_number="DUPLICATE-PAID-WEBHOOK",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_PAYMENT_PENDING.value,
        title="Оплата консультации",
    )
    session.add(case)
    await session.flush()
    original = Payment(
        case_id=case.id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT.value,
        title="Первая подтверждённая оплата",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=PaymentStatus.PAID.value,
        provider="fake",
        provider_payment_id="duplicate-original-paid",
        processing_outcome=PaymentProcessingOutcome.PROCESSED,
        manual_review_required=False,
    )
    retry = Payment(
        case_id=case.id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT.value,
        title="Повторный provider payment",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=PaymentStatus.WAITING_CONFIRMATION.value,
        provider="fake",
        provider_payment_id="duplicate-retry-payment",
        payment_url="https://pay.example.test/duplicate-retry",
        manual_review_required=False,
    )
    session.add_all([original, retry])
    await session.commit()
    return case, original, retry


async def manual_review_count(session, *, case_id: int) -> int:
    return int(
        (
            await session.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id == case_id,
                    AuditLog.action == "PAYMENT_WEBHOOK_REQUIRES_MANUAL_REVIEW",
                )
            )
        ).scalar_one()
    )


async def paid_event_count(session, *, case_id: int) -> int:
    return int(
        (
            await session.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id == case_id,
                    AuditLog.action == "PAYMENT_PAID",
                )
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_second_successful_payment_becomes_manual_review_conflict(
    duplicate_webhook_db,
):
    async with duplicate_webhook_db() as session:
        case, original, retry = await seed_case(session)
        service = PaymentWebhookService(session)

        result = await service.process_successful_payment(
            payment=retry,
            case=case,
            provider_payload={"event": "payment.succeeded", "attempt": 2},
        )
        await session.commit()

        await session.refresh(original)
        await session.refresh(retry)
        assert result.id == retry.id
        assert original.status == PaymentStatus.PAID.value
        assert original.processing_outcome == PaymentProcessingOutcome.PROCESSED
        assert retry.status == PaymentStatus.WAITING_CONFIRMATION.value
        assert retry.processing_outcome == PaymentProcessingOutcome.CONFLICT
        assert retry.manual_review_required is True
        assert retry.processed_at is None
        assert "уже оплачен" in str(retry.processing_error).lower()
        assert await manual_review_count(session, case_id=case.id) == 1
        assert await paid_event_count(session, case_id=case.id) == 0

        repeated = await service.process_successful_payment(
            payment=retry,
            case=case,
            provider_payload={"event": "payment.succeeded", "attempt": 3},
        )
        await session.commit()

        assert repeated.id == retry.id
        assert repeated.processing_outcome == PaymentProcessingOutcome.CONFLICT
        assert await manual_review_count(session, case_id=case.id) == 1
        assert await paid_event_count(session, case_id=case.id) == 0
