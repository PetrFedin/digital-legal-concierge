from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.payments import payment_service as payment_service_module
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
async def payment_integrity_db(tmp_path):
    database_path = tmp_path / "payment-creation-integrity.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def seed_case(session, *, suffix: int = 1):
    user = User(
        telegram_id=996_000 + suffix,
        telegram_username=f"payment_integrity_{suffix}",
        full_name=f"Клиент {suffix}",
    )
    session.add(user)
    await session.flush()
    case = Case(
        case_number=f"PAYMENT-INTEGRITY-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_PAYMENT_PENDING.value,
        title="Оплата консультации",
    )
    session.add(case)
    await session.flush()
    return user, case


async def event_count(session, *, case_id: int, action: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id == case_id,
                    AuditLog.action == action,
                )
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_open_payment_is_reused_and_created_once(payment_integrity_db):
    async with payment_integrity_db() as session:
        _, case = await seed_case(session)
        service = PaymentService(session)

        first = await service.get_or_create_payment(
            case=case,
            payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
            amount=Decimal("5000.00"),
        )
        second = await service.get_or_create_payment(
            case=case,
            payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
            amount=Decimal("9000.00"),
        )

        assert first.id == second.id
        assert first.amount == Decimal("5000.00")
        assert await event_count(
            session,
            case_id=case.id,
            action="PAYMENT_CREATED",
        ) == 1
        assert int(
            (
                await session.execute(
                    select(func.count(Payment.id)).where(Payment.case_id == case.id)
                )
            ).scalar_one()
        ) == 1


@pytest.mark.asyncio
async def test_multiple_open_payments_stop_automatic_payment(payment_integrity_db):
    async with payment_integrity_db() as session:
        _, case = await seed_case(session, suffix=2)
        session.add_all(
            [
                Payment(
                    case_id=case.id,
                    payment_code=PaymentCode.M2_CONSULTATION_PAYMENT.value,
                    title="Оплата консультации",
                    amount=Decimal("5000.00"),
                    currency="RUB",
                    status=PaymentStatus.PENDING.value,
                ),
                Payment(
                    case_id=case.id,
                    payment_code=PaymentCode.M2_CONSULTATION_PAYMENT.value,
                    title="Оплата консультации",
                    amount=Decimal("5000.00"),
                    currency="RUB",
                    status=PaymentStatus.WAITING_CONFIRMATION.value,
                ),
            ]
        )
        await session.flush()

        with pytest.raises(PaymentIntegrityError, match="несколько открытых"):
            await PaymentService(session).get_or_create_payment(
                case=case,
                payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
                amount=Decimal("5000.00"),
            )

        assert await event_count(
            session,
            case_id=case.id,
            action="PAYMENT_CREATED",
        ) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-1.00")])
async def test_nonpositive_payment_amount_is_rejected(payment_integrity_db, amount):
    async with payment_integrity_db() as session:
        _, case = await seed_case(session, suffix=3 if amount == 0 else 4)

        with pytest.raises(PaymentIntegrityError, match="Сумма платежа"):
            await PaymentService(session).get_or_create_payment(
                case=case,
                payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
                amount=amount,
            )

        assert int(
            (
                await session.execute(
                    select(func.count(Payment.id)).where(Payment.case_id == case.id)
                )
            ).scalar_one()
        ) == 0


@pytest.mark.asyncio
async def test_payment_link_creation_is_idempotent(payment_integrity_db, monkeypatch):
    async with payment_integrity_db() as session:
        _, case = await seed_case(session, suffix=5)
        payment = await PaymentService(session).get_or_create_payment(
            case=case,
            payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
            amount=Decimal("5000.00"),
        )
        calls = 0

        class FakeProvider:
            async def create_payment(self, **kwargs):
                nonlocal calls
                calls += 1
                return SimpleNamespace(
                    provider="fake",
                    provider_payment_id="provider-500",
                    payment_url="https://pay.example.test/500",
                )

        monkeypatch.setattr(
            payment_service_module,
            "get_payment_provider",
            lambda: FakeProvider(),
        )
        service = PaymentService(session)

        first = await service.create_payment_link(payment)
        second = await service.create_payment_link(payment)

        assert first is second is payment
        assert calls == 1
        assert payment.status == PaymentStatus.WAITING_CONFIRMATION.value
        assert payment.payment_url == "https://pay.example.test/500"


@pytest.mark.asyncio
async def test_partial_provider_state_is_not_retried_automatically(
    payment_integrity_db,
    monkeypatch,
):
    async with payment_integrity_db() as session:
        _, case = await seed_case(session, suffix=6)
        payment = await PaymentService(session).get_or_create_payment(
            case=case,
            payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
            amount=Decimal("5000.00"),
        )
        payment.provider = "fake"
        payment.provider_payment_id = "provider-without-url"
        await session.flush()

        monkeypatch.setattr(
            payment_service_module,
            "get_payment_provider",
            lambda: pytest.fail("provider must not be called"),
        )

        with pytest.raises(PaymentIntegrityError, match="ссылка отсутствует"):
            await PaymentService(session).create_payment_link(payment)

        assert payment.payment_url is None
        assert payment.status == PaymentStatus.PENDING.value


@pytest.mark.asyncio
async def test_mark_paid_is_idempotent(payment_integrity_db):
    async with payment_integrity_db() as session:
        _, case = await seed_case(session, suffix=7)
        payment = await PaymentService(session).get_or_create_payment(
            case=case,
            payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
            amount=Decimal("5000.00"),
        )
        service = PaymentService(session)

        first = await service.mark_paid(payment=payment, case=case)
        second = await service.mark_paid(payment=payment, case=case)

        assert first is second is payment
        assert payment.status == PaymentStatus.PAID.value
        assert await event_count(
            session,
            case_id=case.id,
            action="PAYMENT_PAID",
        ) == 1
