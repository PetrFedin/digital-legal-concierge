from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.payment import Payment
from app.models.payment_event import PaymentEvent
from app.models.user import User


_DATABASE_URL = str(settings.database_url)
pytestmark = pytest.mark.skipif(
    not _DATABASE_URL.startswith(("postgresql", "postgres")),
    reason="PostgreSQL payment row-lock concurrency contract",
)


def _telegram_id() -> int:
    return 8_100_000_000_000 + (uuid.uuid4().int % 1_000_000_000)


async def _new_m1_payment_case(*, with_payment: bool) -> tuple[int, int | None]:
    async with AsyncSessionLocal() as db:
        user = User(
            telegram_id=_telegram_id(),
            full_name="Postgres payment concurrency",
        )
        db.add(user)
        await db.flush()
        case = Case(
            case_number=f"PAY-RACE-{uuid.uuid4().hex[:20]}",
            client_id=user.id,
            route="M1",
            status=CaseStatus.M1_WAITING_PAYMENT_30000,
            title="Payment concurrency probe",
        )
        db.add(case)
        await db.flush()
        payment_id: int | None = None
        if with_payment:
            payment = Payment(
                case_id=case.id,
                payment_code=PaymentCode.M1_INITIAL_PAYMENT,
                title="Первый платеж М1",
                amount=Decimal("30000.00"),
                currency="RUB",
                status=PaymentStatus.WAITING_CONFIRMATION,
                provider="pytest",
                provider_payment_id=f"pytest-{uuid.uuid4().hex}",
            )
            db.add(payment)
            await db.flush()
            payment_id = int(payment.id)
        case_id = int(case.id)
        await db.commit()
        return case_id, payment_id


async def _get_or_create_initial_payment(case_id: int) -> int:
    async with AsyncSessionLocal() as db:
        case = await db.get(Case, int(case_id))
        assert case is not None
        payment = await PaymentService(db).get_or_create_payment(
            case=case,
            payment_code=PaymentCode.M1_INITIAL_PAYMENT,
            amount=Decimal("30000.00"),
        )
        payment_id = int(payment.id)
        await db.commit()
        return payment_id


async def _process_success(payment_id: int, case_id: int) -> str:
    async with AsyncSessionLocal() as db:
        payment = await db.get(Payment, int(payment_id))
        case = await db.get(Case, int(case_id))
        assert payment is not None
        assert case is not None
        result = await PaymentWebhookService(db).process_successful_payment(
            payment=payment,
            case=case,
            provider_payload={"source": "pytest-concurrency"},
        )
        status = str(result.status)
        await db.commit()
        return status


async def _scenario_duplicate_payment_creation_is_exactly_once() -> None:
    case_id, _ = await _new_m1_payment_case(with_payment=False)

    first, second = await asyncio.gather(
        _get_or_create_initial_payment(case_id),
        _get_or_create_initial_payment(case_id),
    )

    assert first == second
    async with AsyncSessionLocal() as db:
        payments = list(
            (
                await db.execute(
                    select(Payment).where(
                        Payment.case_id == case_id,
                        Payment.payment_code == PaymentCode.M1_INITIAL_PAYMENT,
                    )
                )
            ).scalars().all()
        )
        assert len(payments) == 1
        assert int(payments[0].id) == first
        assert str(payments[0].status) == PaymentStatus.PENDING.value


async def _scenario_duplicate_success_is_one_money_transition() -> None:
    case_id, payment_id = await _new_m1_payment_case(with_payment=True)
    assert payment_id is not None

    first, second = await asyncio.gather(
        _process_success(payment_id, case_id),
        _process_success(payment_id, case_id),
    )

    assert {first, second} == {PaymentStatus.PAID.value}
    async with AsyncSessionLocal() as db:
        payment = await db.get(Payment, payment_id)
        case = await db.get(Case, case_id)
        assert payment is not None
        assert case is not None
        assert str(payment.status) == PaymentStatus.PAID.value
        assert payment.paid_at is not None
        assert str(case.status) == CaseStatus.M1_POWER_OF_ATTORNEY.value

        paid_transition_count = await db.scalar(
            select(func.count(PaymentEvent.id)).where(
                PaymentEvent.payment_id == payment_id,
                PaymentEvent.event_type == "STATUS_CHANGED",
                PaymentEvent.status_after == PaymentStatus.PAID.value,
            )
        )
        assert paid_transition_count == 1


def test_same_m1_payment_attempt_is_exactly_once_under_postgres_concurrency() -> None:
    asyncio.run(_scenario_duplicate_payment_creation_is_exactly_once())


def test_duplicate_success_is_exactly_one_paid_transition_under_postgres_concurrency() -> None:
    asyncio.run(_scenario_duplicate_success_is_one_money_transition())
