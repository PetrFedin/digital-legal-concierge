from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.payments.payment_service import PaymentIntegrityError, PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.payment import Payment
from app.models.user import User


@pytest.fixture
async def paid_stage_db(tmp_path):
    database_path = tmp_path / "paid-payment-stage-integrity.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def seed_case(session, *, suffix: int) -> Case:
    user = User(
        telegram_id=1_060_000 + suffix,
        telegram_username=f"paid_stage_{suffix}",
        full_name=f"Клиент {suffix}",
    )
    session.add(user)
    await session.flush()
    case = Case(
        case_number=f"PAID-STAGE-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_PAYMENT_PENDING.value,
        title="Оплата консультации",
    )
    session.add(case)
    await session.flush()
    return case


def payment(case_id: int, *, status: str, suffix: int) -> Payment:
    return Payment(
        case_id=case_id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT.value,
        title="Оплата консультации",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=status,
        provider="fake",
        provider_payment_id=f"paid-stage-provider-{suffix}",
    )


async def paid_audit_count(session, *, case_id: int) -> int:
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
async def test_new_payment_link_is_blocked_after_stage_is_paid(paid_stage_db):
    async with paid_stage_db() as session:
        case = await seed_case(session, suffix=1)
        paid = payment(case.id, status=PaymentStatus.PAID.value, suffix=1)
        session.add(paid)
        await session.flush()

        with pytest.raises(PaymentIntegrityError, match="уже оплачен"):
            await PaymentService(session).get_or_create_payment(
                case=case,
                payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
                amount=Decimal("5000.00"),
            )

        rows = list(
            (
                await session.execute(
                    select(Payment).where(Payment.case_id == case.id)
                )
            ).scalars()
        )
        assert [row.id for row in rows] == [paid.id]


@pytest.mark.asyncio
async def test_marking_second_payment_paid_is_financial_conflict(paid_stage_db):
    async with paid_stage_db() as session:
        case = await seed_case(session, suffix=2)
        original = payment(case.id, status=PaymentStatus.PAID.value, suffix=2)
        retry = payment(
            case.id,
            status=PaymentStatus.WAITING_CONFIRMATION.value,
            suffix=3,
        )
        session.add_all([original, retry])
        await session.flush()

        with pytest.raises(PaymentIntegrityError, match="другим платежом"):
            await PaymentService(session).mark_paid(
                payment=retry,
                case=case,
                actor_type="payment_provider",
            )

        await session.refresh(original)
        await session.refresh(retry)
        assert original.status == PaymentStatus.PAID.value
        assert retry.status == PaymentStatus.WAITING_CONFIRMATION.value
        assert await paid_audit_count(session, case_id=case.id) == 0


@pytest.mark.asyncio
async def test_database_rejects_two_paid_payments_for_same_stage(paid_stage_db):
    async with paid_stage_db() as session:
        case = await seed_case(session, suffix=4)
        session.add_all(
            [
                payment(case.id, status=PaymentStatus.PAID.value, suffix=4),
                payment(case.id, status=PaymentStatus.PAID.value, suffix=5),
            ]
        )

        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.asyncio
async def test_refunded_history_does_not_block_new_paid_stage_record(paid_stage_db):
    async with paid_stage_db() as session:
        case = await seed_case(session, suffix=6)
        session.add_all(
            [
                payment(case.id, status=PaymentStatus.REFUNDED.value, suffix=6),
                payment(case.id, status=PaymentStatus.PAID.value, suffix=7),
            ]
        )

        await session.flush()


@pytest.mark.asyncio
async def test_service_detects_legacy_duplicate_paid_rows(paid_stage_db):
    async with paid_stage_db() as session:
        case = await seed_case(session, suffix=8)
        await session.execute(text("DROP INDEX uq_payments_paid_case_code"))
        session.add_all(
            [
                payment(case.id, status=PaymentStatus.PAID.value, suffix=8),
                payment(case.id, status=PaymentStatus.PAID.value, suffix=9),
            ]
        )
        await session.flush()

        with pytest.raises(PaymentIntegrityError, match="несколько подтверждённых"):
            await PaymentService(session).get_or_create_payment(
                case=case,
                payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
                amount=Decimal("5000.00"),
            )
