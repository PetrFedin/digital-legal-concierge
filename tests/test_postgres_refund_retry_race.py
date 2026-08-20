from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.refund_service import ConsultationRefundService
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.payment import Payment
from app.models.payment_event import PaymentEvent
from app.models.user import User


_DATABASE_URL = str(settings.database_url)
pytestmark = pytest.mark.skipif(
    not _DATABASE_URL.startswith(("postgresql", "postgres")),
    reason="PostgreSQL refund retry/resolution row-lock concurrency contract",
)


def _telegram_id() -> int:
    return 8_400_000_000_000 + (uuid.uuid4().int % 1_000_000_000)


async def _seed_declined_refund() -> tuple[int, int]:
    async with AsyncSessionLocal() as db:
        user = User(
            telegram_id=_telegram_id(),
            full_name="Postgres refund retry race",
        )
        db.add(user)
        await db.flush()

        case = Case(
            case_number=f"REFUND-RACE-{uuid.uuid4().hex[:18]}",
            client_id=user.id,
            route="M1",
            status=CaseStatus.M1_WAITING_PAYMENT_30000,
            title="Refund retry versus resolution race",
        )
        db.add(case)
        await db.flush()

        payment = Payment(
            case_id=case.id,
            payment_code=PaymentCode.M1_INITIAL_PAYMENT,
            title="Возврат отклонённого платежа M1",
            amount=Decimal("30000.00"),
            currency="RUB",
            status=PaymentStatus.REFUND_DECLINED,
            provider="pytest",
            provider_payment_id=f"pytest-refund-race-{uuid.uuid4().hex}",
        )
        db.add(payment)
        await db.flush()
        result = int(case.id), int(payment.id)
        await db.commit()
        return result


async def _retry(payment_id: int) -> str:
    async with AsyncSessionLocal() as db:
        try:
            payment, _ = await ConsultationRefundService(db).reopen_declined_refund(
                payment_id=payment_id,
                actor_id=None,
                comment="PostgreSQL retry after provider rejection was fixed",
            )
            status = str(payment.status)
            await db.commit()
            return status
        except Exception:
            await db.rollback()
            raise


async def _resolve(payment_id: int) -> str:
    async with AsyncSessionLocal() as db:
        try:
            payment = await ConsultationRefundService(db).resolve_refund(
                payment_id=payment_id,
                decision="refunded",
                actor_id=None,
                comment="PostgreSQL concurrent factual refund confirmation",
            )
            status = str(payment.status)
            await db.commit()
            return status
        except Exception:
            await db.rollback()
            raise


async def _scenario_retry_and_resolution_are_serializable() -> None:
    case_id, payment_id = await _seed_declined_refund()

    results = await asyncio.gather(
        _retry(payment_id),
        _resolve(payment_id),
        return_exceptions=True,
    )

    async with AsyncSessionLocal() as db:
        payment = await db.get(Payment, payment_id)
        case = await db.get(Case, case_id)
        assert payment is not None
        assert case is not None

        final_status = str(payment.status)
        assert final_status in {
            PaymentStatus.REFUND_PENDING.value,
            PaymentStatus.REFUNDED.value,
        }
        assert str(case.status) == CaseStatus.M1_WAITING_PAYMENT_30000.value

        pending_count = await db.scalar(
            select(func.count(PaymentEvent.id)).where(
                PaymentEvent.payment_id == payment_id,
                PaymentEvent.event_type == "STATUS_CHANGED",
                PaymentEvent.status_after == PaymentStatus.REFUND_PENDING.value,
            )
        )
        refunded_count = await db.scalar(
            select(func.count(PaymentEvent.id)).where(
                PaymentEvent.payment_id == payment_id,
                PaymentEvent.event_type == "STATUS_CHANGED",
                PaymentEvent.status_after == PaymentStatus.REFUNDED.value,
            )
        )
        assert pending_count == 1
        if final_status == PaymentStatus.REFUNDED.value:
            assert refunded_count == 1
            assert payment.refunded_at is not None
            assert all(not isinstance(result, Exception) for result in results)
        else:
            # Resolution took the Payment lock first, correctly rejected an
            # attempt to resolve REFUND_DECLINED directly, and retry then reopened
            # the existing workflow. No illegal DECLINED -> REFUNDED jump occurs.
            assert refunded_count == 0
            errors = [result for result in results if isinstance(result, Exception)]
            assert len(errors) == 1
            assert isinstance(errors[0], ValueError)


def test_refund_retry_and_resolution_have_only_serializable_outcomes_under_postgres() -> None:
    asyncio.run(_scenario_retry_and_resolution_are_serializable())
