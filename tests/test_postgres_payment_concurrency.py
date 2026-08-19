from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.domain.consultations.slot_service import SlotService
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.domain.payments.refund_service import ConsultationRefundService
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
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


async def _new_refund_pending_m1_payment() -> tuple[int, int]:
    case_id, _ = await _new_m1_payment_case(with_payment=False)
    async with AsyncSessionLocal() as db:
        payment = Payment(
            case_id=case_id,
            payment_code=PaymentCode.M1_INITIAL_PAYMENT,
            title="Возврат устаревшего платежа M1",
            amount=Decimal("30000.00"),
            currency="RUB",
            status=PaymentStatus.REFUND_PENDING,
            provider="pytest",
            provider_payment_id=f"pytest-refund-{uuid.uuid4().hex}",
        )
        db.add(payment)
        await db.flush()
        payment_id = int(payment.id)
        await db.commit()
        return case_id, payment_id


async def _new_expired_m2_payment_reservation() -> tuple[int, int, int, int]:
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        user = User(
            telegram_id=_telegram_id(),
            full_name="Postgres M2 race client",
        )
        lawyer = Lawyer(
            full_name="Postgres M2 race lawyer",
            email=f"race-{uuid.uuid4().hex}@example.test",
            is_active=True,
        )
        db.add_all([user, lawyer])
        await db.flush()

        case = Case(
            case_number=f"M2-RACE-{uuid.uuid4().hex[:20]}",
            client_id=user.id,
            route="M2",
            status=CaseStatus.M2_PAYMENT_PENDING,
            title="M2 payment versus hold expiry",
        )
        db.add(case)
        await db.flush()

        consultation = Consultation(
            case_id=case.id,
            lawyer_id=lawyer.id,
            status=ConsultationStatus.PAYMENT_PENDING,
            consultation_type="online",
            subject_type="new_or_other",
            client_description="PostgreSQL concurrency probe",
        )
        db.add(consultation)
        await db.flush()

        starts_at = now + timedelta(days=1)
        slot = ConsultationSlot(
            lawyer_id=lawyer.id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            status="held",
            hold_expires_at=now - timedelta(seconds=5),
            held_by_user_id=user.id,
            consultation_id=consultation.id,
        )
        db.add(slot)
        await db.flush()

        consultation.slot_id = slot.id
        consultation.scheduled_at = starts_at
        payment = Payment(
            case_id=case.id,
            payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
            title="Оплата консультации",
            amount=Decimal("5000.00"),
            currency="RUB",
            status=PaymentStatus.PENDING,
            provider="pytest",
            provider_payment_id=f"pytest-m2-{uuid.uuid4().hex}",
            reservation_key=PaymentService.consultation_reservation_key(
                consultation.id,
                slot.id,
            ),
        )
        db.add(payment)
        await db.flush()

        result = (
            int(case.id),
            int(consultation.id),
            int(slot.id),
            int(payment.id),
        )
        await db.commit()
        return result


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


async def _resolve_refund(payment_id: int) -> str:
    async with AsyncSessionLocal() as db:
        payment = await ConsultationRefundService(db).resolve_refund(
            payment_id=payment_id,
            decision="refunded",
            actor_id=None,
            comment="PostgreSQL concurrent refund confirmation",
        )
        status = str(payment.status)
        await db.commit()
        return status


async def _release_expired_holds() -> int:
    async with AsyncSessionLocal() as db:
        released = await SlotService(db).release_expired_holds()
        await db.commit()
        return int(released)


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


async def _scenario_duplicate_refund_confirmation_is_exactly_once() -> None:
    case_id, payment_id = await _new_refund_pending_m1_payment()

    first, second = await asyncio.gather(
        _resolve_refund(payment_id),
        _resolve_refund(payment_id),
    )
    assert {first, second} == {PaymentStatus.REFUNDED.value}

    async with AsyncSessionLocal() as db:
        payment = await db.get(Payment, payment_id)
        case = await db.get(Case, case_id)
        assert payment is not None
        assert case is not None
        assert str(payment.status) == PaymentStatus.REFUNDED.value
        assert payment.refunded_at is not None
        assert str(case.status) == CaseStatus.M1_WAITING_PAYMENT_30000.value

        refunded_transition_count = await db.scalar(
            select(func.count(PaymentEvent.id)).where(
                PaymentEvent.payment_id == payment_id,
                PaymentEvent.event_type == "STATUS_CHANGED",
                PaymentEvent.status_after == PaymentStatus.REFUNDED.value,
            )
        )
        assert refunded_transition_count == 1


async def _scenario_m2_expiry_and_success_never_resurrect_expired_slot() -> None:
    case_id, consultation_id, slot_id, payment_id = (
        await _new_expired_m2_payment_reservation()
    )

    await asyncio.gather(
        _release_expired_holds(),
        _process_success(payment_id, case_id),
    )

    async with AsyncSessionLocal() as db:
        payment = await db.get(Payment, payment_id)
        case = await db.get(Case, case_id)
        consultation = await db.get(Consultation, consultation_id)
        slot = await db.get(ConsultationSlot, slot_id)
        assert payment is not None
        assert case is not None
        assert consultation is not None
        assert slot is not None

        # Money received after an expired reservation is a review fact. It must
        # never silently recreate the no-longer-valid booking.
        assert str(payment.status) == PaymentStatus.PAID_REVIEW.value
        assert payment.paid_at is not None
        assert payment.expired_at is not None
        assert str(case.status) == CaseStatus.M2_SLOT_PENDING.value
        assert str(consultation.status) == ConsultationStatus.SLOT_PENDING.value
        assert consultation.slot_id is None
        assert consultation.scheduled_at is None
        assert str(slot.status) == "available"
        assert slot.consultation_id is None
        assert slot.held_by_user_id is None
        assert slot.hold_expires_at is None

        paid_count = await db.scalar(
            select(func.count(PaymentEvent.id)).where(
                PaymentEvent.payment_id == payment_id,
                PaymentEvent.event_type == "STATUS_CHANGED",
                PaymentEvent.status_after == PaymentStatus.PAID.value,
            )
        )
        review_count = await db.scalar(
            select(func.count(PaymentEvent.id)).where(
                PaymentEvent.payment_id == payment_id,
                PaymentEvent.event_type == "STATUS_CHANGED",
                PaymentEvent.status_after == PaymentStatus.PAID_REVIEW.value,
            )
        )
        assert paid_count == 0
        assert review_count == 1


def test_same_m1_payment_attempt_is_exactly_once_under_postgres_concurrency() -> None:
    asyncio.run(_scenario_duplicate_payment_creation_is_exactly_once())


def test_duplicate_success_is_exactly_one_paid_transition_under_postgres_concurrency() -> None:
    asyncio.run(_scenario_duplicate_success_is_one_money_transition())


def test_duplicate_refund_confirmation_is_exactly_once_under_postgres_concurrency() -> None:
    asyncio.run(_scenario_duplicate_refund_confirmation_is_exactly_once())


def test_m2_expiry_vs_success_never_resurrects_expired_booking_under_postgres() -> None:
    asyncio.run(_scenario_m2_expiry_and_success_never_resurrect_expired_slot())
