from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.consultation_change_service import ConsultationChangeService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.payment import Payment
from app.models.user import User


@asynccontextmanager
async def database(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'm2-change-finance-v36.db'}"
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield factory
    finally:
        await engine.dispose()


async def seed_booked_consultation(
    session,
    *,
    telegram_id: int,
    case_number: str,
):
    user = User(telegram_id=telegram_id, full_name=f"Клиент {case_number}")
    lawyer = Lawyer(full_name=f"Юрист {case_number}", is_active=True)
    session.add_all([user, lawyer])
    await session.flush()

    case = Case(
        case_number=case_number,
        client_id=user.id,
        route=RouteCode.M2,
        status=CaseStatus.M2_CONSULTATION_BOOKED,
        title="Консультация",
    )
    session.add(case)
    await session.flush()

    starts_at = datetime.now(timezone.utc) + timedelta(days=3)
    consultation = Consultation(
        case_id=case.id,
        status=ConsultationStatus.BOOKED,
        client_description=(
            "Нужна консультация по договору и понятный следующий юридический шаг."
        ),
        subject_type="new_or_other",
        lawyer_id=lawyer.id,
        scheduled_at=starts_at,
    )
    session.add(consultation)
    await session.flush()

    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        status="booked",
        consultation_id=consultation.id,
    )
    session.add(slot)
    await session.flush()
    consultation.slot_id = slot.id
    await session.flush()
    return user, lawyer, case, consultation, slot


def consultation_payment(
    *,
    case_id: int,
    consultation_id: int,
    slot_id: int,
    status: PaymentStatus,
    amount: str = "5000.00",
):
    return Payment(
        case_id=case_id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
        title="Оплата консультации",
        amount=Decimal(amount),
        currency="RUB",
        status=status,
        reservation_key=f"consultation:{consultation_id}:slot:{slot_id}",
    )


@pytest.mark.asyncio
async def test_free_booking_does_not_invent_refund_if_provider_is_enabled_later(tmp_path):
    async with database(tmp_path) as factory:
        async with factory() as session:
            user, _lawyer, case, consultation, _slot = await seed_booked_consultation(
                session,
                telegram_id=983301,
                case_number="M2-FREE-CANCEL-V36",
            )
            await session.commit()

            cancelled, replacement, changed_case, refund_required = (
                await ConsultationChangeService(session).cancel_and_prepare_rebooking(
                    consultation=consultation,
                    case=case,
                    client_id=user.id,
                    comment="Нужно перенести встречу на другую дату",
                    # Provider is enabled now, but this consultation was booked
                    # with no payment and therefore has nothing to refund.
                    payments_currently_disabled=False,
                )
            )
            await session.commit()

            assert refund_required is False
            assert cancelled.status == ConsultationStatus.CANCELLED
            assert replacement.id != consultation.id
            assert replacement.client_description == consultation.client_description
            assert changed_case.status == CaseStatus.M2_SLOT_PENDING
            assert (
                await session.execute(select(Payment).where(Payment.case_id == case.id))
            ).scalars().all() == []


@pytest.mark.asyncio
async def test_paid_booking_requires_refund_even_if_provider_is_disabled_later(tmp_path):
    async with database(tmp_path) as factory:
        async with factory() as session:
            user, _lawyer, case, consultation, slot = await seed_booked_consultation(
                session,
                telegram_id=983302,
                case_number="M2-PAID-CANCEL-V36",
            )
            payment = consultation_payment(
                case_id=case.id,
                consultation_id=consultation.id,
                slot_id=slot.id,
                status=PaymentStatus.PAID,
            )
            session.add(payment)
            await session.commit()

            _cancelled, _replacement, changed_case, refund_required = (
                await ConsultationChangeService(session).cancel_and_prepare_rebooking(
                    consultation=consultation,
                    case=case,
                    client_id=user.id,
                    comment="Нужно отменить и выбрать новое время",
                    payments_currently_disabled=True,
                )
            )
            await session.commit()
            await session.refresh(payment)

            assert refund_required is True
            assert payment.status == PaymentStatus.REFUND_PENDING
            assert changed_case.status == CaseStatus.M2_SLOT_PENDING


@pytest.mark.asyncio
async def test_old_refunded_payment_from_other_consultation_does_not_taint_new_free_booking(
    tmp_path,
):
    async with database(tmp_path) as factory:
        async with factory() as session:
            user, _lawyer, case, current, current_slot = await seed_booked_consultation(
                session,
                telegram_id=983303,
                case_number="M2-OLD-REFUND-V36",
            )

            old = Consultation(
                case_id=case.id,
                status=ConsultationStatus.CANCELLED,
                client_description="Предыдущая консультация",
                subject_type="new_or_other",
            )
            session.add(old)
            await session.flush()
            old_payment = Payment(
                case_id=case.id,
                payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
                title="Старая консультация",
                amount=Decimal("5000.00"),
                currency="RUB",
                status=PaymentStatus.REFUNDED,
                reservation_key=f"consultation:{old.id}:slot:999001",
            )
            session.add(old_payment)
            await session.commit()

            cancelled, replacement, _case, refund_required = (
                await ConsultationChangeService(session).cancel_and_prepare_rebooking(
                    consultation=current,
                    case=case,
                    client_id=user.id,
                    comment="Перенос бесплатной новой консультации",
                    payments_currently_disabled=False,
                )
            )
            await session.commit()
            await session.refresh(old_payment)
            await session.refresh(current_slot)

            assert refund_required is False
            assert cancelled.status == ConsultationStatus.CANCELLED
            assert replacement.id != current.id
            assert old_payment.status == PaymentStatus.REFUNDED
            assert current_slot.status == "free"


@pytest.mark.asyncio
async def test_paid_review_is_treated_as_received_money_and_enters_refund_flow(tmp_path):
    async with database(tmp_path) as factory:
        async with factory() as session:
            user, _lawyer, case, consultation, slot = await seed_booked_consultation(
                session,
                telegram_id=983304,
                case_number="M2-PAID-REVIEW-V36",
            )
            payment = consultation_payment(
                case_id=case.id,
                consultation_id=consultation.id,
                slot_id=slot.id,
                status=PaymentStatus.PAID_REVIEW,
            )
            session.add(payment)
            await session.commit()

            _cancelled, _replacement, _case, refund_required = (
                await ConsultationChangeService(session).cancel_and_prepare_rebooking(
                    consultation=consultation,
                    case=case,
                    client_id=user.id,
                    comment="Отмена записи с полученными деньгами на проверке",
                    payments_currently_disabled=True,
                )
            )
            await session.commit()
            await session.refresh(payment)

            assert refund_required is True
            assert payment.status == PaymentStatus.REFUND_PENDING


@pytest.mark.asyncio
async def test_active_unpaid_link_is_expired_when_consultation_is_cancelled(tmp_path):
    async with database(tmp_path) as factory:
        async with factory() as session:
            user, _lawyer, case, consultation, slot = await seed_booked_consultation(
                session,
                telegram_id=983305,
                case_number="M2-STALE-LINK-V36",
            )
            payment = consultation_payment(
                case_id=case.id,
                consultation_id=consultation.id,
                slot_id=slot.id,
                status=PaymentStatus.WAITING_CONFIRMATION,
            )
            payment.payment_url = "pay://stale-consultation-link"
            session.add(payment)
            await session.commit()

            _cancelled, _replacement, _case, refund_required = (
                await ConsultationChangeService(session).cancel_and_prepare_rebooking(
                    consultation=consultation,
                    case=case,
                    client_id=user.id,
                    comment="Отмена записи до поступления денег",
                    payments_currently_disabled=False,
                )
            )
            await session.commit()
            await session.refresh(payment)

            assert refund_required is False
            assert payment.status == PaymentStatus.EXPIRED
