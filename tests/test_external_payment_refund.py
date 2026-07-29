from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.refund_service import PaymentRefundError, PaymentRefundService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.payment import Payment
from app.models.user import User


@pytest.fixture
async def refund_db(tmp_path):
    database_path = tmp_path / "external-payment-refund.db"
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
        telegram_id=1_090_000 + suffix,
        telegram_username=f"refund_{suffix}",
        full_name=f"Клиент {suffix}",
    )
    session.add(user)
    await session.flush()
    case = Case(
        case_number=f"REFUND-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_PAYMENT_PENDING.value,
        title="Оплата консультации",
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
        provider_payment_id=f"refund-payment-{suffix}",
        payment_url="https://pay.example.test/refund",
    )
    session.add(payment)
    await session.commit()
    return case, payment


async def refund_event_count(session, *, case_id: int) -> int:
    return int(
        (
            await session.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id == case_id,
                    AuditLog.action == PaymentRefundService.ACTION,
                )
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_full_external_refund_is_recorded_once(refund_db):
    async with refund_db() as session:
        case, payment = await seed_payment(
            session,
            suffix=1,
            status=PaymentStatus.PAID.value,
        )
        service = PaymentRefundService(session)

        first = await service.confirm_external_refund(
            payment=payment,
            case=case,
            actor_id=9101,
            refund_reference="refund-provider-001",
            comment="Полный возврат подтверждён в кабинете провайдера",
            source="admin_test",
        )
        second = await service.confirm_external_refund(
            payment=payment,
            case=case,
            actor_id=9101,
            refund_reference="refund-provider-001",
            comment="Повторное подтверждение",
            source="admin_test_retry",
        )
        await session.commit()

        await session.refresh(payment)
        assert first.id == second.id
        assert payment.status == PaymentStatus.REFUNDED.value
        assert payment.payment_url is None
        assert first.actor_type == "admin"
        assert first.actor_id == 9101
        assert first.new_value["refund_reference"] == "refund-provider-001"
        assert first.new_value["amount"] == "5000.00"
        assert first.new_value["source"] == "admin_test"
        assert await refund_event_count(session, case_id=case.id) == 1


@pytest.mark.asyncio
async def test_partial_refund_is_rejected(refund_db):
    async with refund_db() as session:
        case, payment = await seed_payment(
            session,
            suffix=2,
            status=PaymentStatus.PAID.value,
        )

        with pytest.raises(PaymentRefundError, match="Частичный возврат"):
            await PaymentRefundService(session).confirm_external_refund(
                payment=payment,
                case=case,
                actor_id=9102,
                refund_reference="partial-refund",
                comment="Попытка частичного возврата",
                amount=Decimal("1000.00"),
            )

        await session.rollback()
        await session.refresh(payment)
        assert payment.status == PaymentStatus.PAID.value
        assert await refund_event_count(session, case_id=case.id) == 0


@pytest.mark.asyncio
async def test_non_paid_payment_cannot_be_refunded(refund_db):
    async with refund_db() as session:
        case, payment = await seed_payment(
            session,
            suffix=3,
            status=PaymentStatus.WAITING_CONFIRMATION.value,
        )

        with pytest.raises(PaymentRefundError, match="статусе PAID"):
            await PaymentRefundService(session).confirm_external_refund(
                payment=payment,
                case=case,
                actor_id=9103,
                refund_reference="not-paid-refund",
                comment="Платёж ещё не подтверждён",
            )

        assert payment.status == PaymentStatus.WAITING_CONFIRMATION.value


@pytest.mark.asyncio
async def test_different_refund_reference_is_conflict(refund_db):
    async with refund_db() as session:
        case, payment = await seed_payment(
            session,
            suffix=4,
            status=PaymentStatus.PAID.value,
        )
        service = PaymentRefundService(session)
        await service.confirm_external_refund(
            payment=payment,
            case=case,
            actor_id=9104,
            refund_reference="refund-reference-a",
            comment="Первое подтверждение",
        )

        with pytest.raises(PaymentRefundError, match="другой reference"):
            await service.confirm_external_refund(
                payment=payment,
                case=case,
                actor_id=9104,
                refund_reference="refund-reference-b",
                comment="Конфликтующий reference",
            )


@pytest.mark.asyncio
async def test_new_stage_payment_can_be_created_after_full_refund(refund_db):
    async with refund_db() as session:
        case, payment = await seed_payment(
            session,
            suffix=5,
            status=PaymentStatus.PAID.value,
        )
        await PaymentRefundService(session).confirm_external_refund(
            payment=payment,
            case=case,
            actor_id=9105,
            refund_reference="refund-before-new-payment",
            comment="Возврат завершён",
        )

        replacement = await PaymentService(session).get_or_create_payment(
            case=case,
            payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
            amount=Decimal("5000.00"),
        )
        await session.commit()

        await session.refresh(payment)
        assert payment.status == PaymentStatus.REFUNDED.value
        assert replacement.id != payment.id
        assert replacement.status == PaymentStatus.PENDING.value
