from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.cancellation_request_service import (
    ConsultationCancellationRequestService,
)
from app.domain.consultations.cancellation_resolution_service import (
    CancellationResolutionDecision,
    CancellationResolutionError,
    ConsultationCancellationResolutionService,
)
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


@pytest.fixture
async def cancellation_resolution_db(tmp_path):
    database_path = tmp_path / "paid-cancellation-resolution.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def seed_requested_cancellation(session, *, suffix: int, payment_status: str):
    starts_at = datetime.now(timezone.utc) + timedelta(days=2)
    user = User(
        telegram_id=1_080_000 + suffix,
        telegram_username=f"cancel_resolution_{suffix}",
        full_name=f"Клиент {suffix}",
    )
    lawyer = Lawyer(
        full_name=f"Юрист {suffix}",
        workload_limit=10,
        is_active=True,
    )
    session.add_all([user, lawyer])
    await session.flush()
    case = Case(
        case_number=f"CANCEL-RESOLUTION-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_CONSULTATION_BOOKED.value,
        title="Оплаченная консультация",
        next_action="Ожидайте консультации в выбранное время",
        assigned_lawyer_id=lawyer.id,
    )
    session.add(case)
    await session.flush()
    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        status=ConsultationStatus.BOOKED.value,
        scheduled_at=starts_at,
        consultation_type="online",
    )
    session.add(consultation)
    await session.flush()
    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=45),
        status="booked",
        held_by_user_id=user.id,
        consultation_id=consultation.id,
    )
    session.add(slot)
    await session.flush()
    consultation.slot_id = slot.id
    payment = Payment(
        case_id=case.id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT.value,
        title="Оплата консультации",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=payment_status,
        provider="fake",
        provider_payment_id=f"cancel-resolution-payment-{suffix}",
    )
    session.add(payment)
    await session.flush()
    await ConsultationCancellationRequestService(session).request(
        consultation=consultation,
        case=case,
        client_id=user.id,
        actor_id=user.id,
        source="test",
    )
    await session.commit()
    return user, case, consultation, slot, payment


@pytest.mark.asyncio
async def test_keep_booking_resolution_preserves_paid_consultation(
    cancellation_resolution_db,
):
    async with cancellation_resolution_db() as session:
        _, case, consultation, slot, payment = await seed_requested_cancellation(
            session,
            suffix=1,
            payment_status=PaymentStatus.PAID.value,
        )
        service = ConsultationCancellationResolutionService(session)

        first = await service.resolve(
            case_id=case.id,
            decision=CancellationResolutionDecision.KEEP_BOOKING,
            actor_id=9001,
            comment="Клиент подтвердил сохранение записи",
        )
        second = await service.resolve(
            case_id=case.id,
            decision=CancellationResolutionDecision.KEEP_BOOKING,
            actor_id=9001,
            comment="Повторный запрос",
        )
        await session.commit()

        await session.refresh(case)
        await session.refresh(consultation)
        await session.refresh(slot)
        await session.refresh(payment)
        assert first.id == second.id
        assert first.actor_type == "admin"
        assert first.actor_id == 9001
        assert first.new_value["decision"] == "KEEP_BOOKING"
        assert case.status == CaseStatus.M2_CONSULTATION_BOOKED.value
        assert case.next_action == "Ожидайте консультации в выбранное время"
        assert consultation.status == ConsultationStatus.BOOKED.value
        assert consultation.slot_id == slot.id
        assert slot.status == "booked"
        assert payment.status == PaymentStatus.PAID.value
        assert await service.list_pending() == []


@pytest.mark.asyncio
async def test_paid_consultation_cannot_close_before_refund(
    cancellation_resolution_db,
):
    async with cancellation_resolution_db() as session:
        _, case, consultation, slot, payment = await seed_requested_cancellation(
            session,
            suffix=2,
            payment_status=PaymentStatus.PAID.value,
        )

        with pytest.raises(CancellationResolutionError, match="возврата"):
            await ConsultationCancellationResolutionService(session).resolve(
                case_id=case.id,
                decision=CancellationResolutionDecision.REFUND_CONFIRMED,
                actor_id=9002,
                comment="Запрошено закрытие без возврата",
            )

        await session.rollback()
        await session.refresh(case)
        await session.refresh(consultation)
        await session.refresh(slot)
        await session.refresh(payment)
        assert case.status == CaseStatus.M2_CONSULTATION_BOOKED.value
        assert consultation.status == ConsultationStatus.BOOKED.value
        assert consultation.slot_id == slot.id
        assert slot.status == "booked"
        assert payment.status == PaymentStatus.PAID.value


@pytest.mark.asyncio
async def test_refund_confirmed_resolution_closes_case_and_releases_slot(
    cancellation_resolution_db,
):
    async with cancellation_resolution_db() as session:
        _, case, consultation, slot, payment = await seed_requested_cancellation(
            session,
            suffix=3,
            payment_status=PaymentStatus.REFUNDED.value,
        )
        service = ConsultationCancellationResolutionService(session)

        resolution = await service.resolve(
            case_id=case.id,
            decision=CancellationResolutionDecision.REFUND_CONFIRMED,
            actor_id=9003,
            comment="Возврат подтверждён провайдером",
            source="admin_test",
        )
        await session.commit()

        await session.refresh(case)
        await session.refresh(consultation)
        await session.refresh(slot)
        await session.refresh(payment)
        assert resolution.new_value["decision"] == "REFUND_CONFIRMED"
        assert resolution.new_value["source"] == "admin_test"
        assert case.status == CaseStatus.M2_CLOSED.value
        assert case.next_action == (
            "Консультация отменена после подтверждённого возврата"
        )
        assert consultation.status == ConsultationStatus.CANCELLED.value
        assert consultation.slot_id is None
        assert slot.status == "available"
        assert slot.consultation_id is None
        assert slot.held_by_user_id is None
        assert payment.status == PaymentStatus.REFUNDED.value
        assert await service.list_pending() == []


@pytest.mark.asyncio
async def test_pending_queue_contains_unresolved_request(cancellation_resolution_db):
    async with cancellation_resolution_db() as session:
        user, case, consultation, slot, _ = await seed_requested_cancellation(
            session,
            suffix=4,
            payment_status=PaymentStatus.PAID.value,
        )

        pending = await ConsultationCancellationResolutionService(
            session
        ).list_pending()

        assert len(pending) == 1
        assert pending[0].case_id == case.id
        assert pending[0].case_number == case.case_number
        assert pending[0].client_id == user.id
        assert pending[0].consultation_id == consultation.id
        assert pending[0].slot_id == slot.id
