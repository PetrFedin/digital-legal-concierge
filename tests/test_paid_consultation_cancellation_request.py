from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.cancellation_request_service import (
    ConsultationCancellationRequestService,
)
from app.domain.consultations.consultation_service import (
    ActiveConsultationConflictError,
    ConsultationNotFoundError,
)
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.payment import Payment
from app.models.user import User


@pytest.fixture
async def cancellation_request_db(tmp_path):
    database_path = tmp_path / "paid-cancellation-request.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def seed_paid_booking(
    session,
    *,
    suffix: int,
    consultation_status: str = ConsultationStatus.BOOKED.value,
):
    starts_at = datetime.now(timezone.utc) + timedelta(days=2)
    user = User(
        telegram_id=999_000 + suffix,
        telegram_username=f"cancel_request_{suffix}",
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
        case_number=f"CANCEL-REQUEST-{suffix}",
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
        status=consultation_status,
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
        status=PaymentStatus.PAID.value,
        provider="fake",
        provider_payment_id=f"paid-cancel-{suffix}",
    )
    session.add(payment)
    await session.flush()
    return user, case, consultation, slot, payment


async def request_count(session, *, case_id: int) -> int:
    return int(
        (
            await session.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id == case_id,
                    AuditLog.action
                    == ConsultationCancellationRequestService.ACTION,
                )
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_paid_cancellation_request_preserves_booking_and_payment(
    cancellation_request_db,
):
    async with cancellation_request_db() as session:
        user, case, consultation, slot, payment = await seed_paid_booking(
            session,
            suffix=1,
        )
        service = ConsultationCancellationRequestService(session)

        first = await service.request(
            consultation=consultation,
            case=case,
            client_id=user.id,
            actor_id=user.id,
            source="test",
        )
        second = await service.request(
            consultation=consultation,
            case=case,
            client_id=user.id,
            actor_id=user.id,
            source="test-retry",
        )
        await session.commit()

        await session.refresh(case)
        await session.refresh(consultation)
        await session.refresh(slot)
        await session.refresh(payment)
        assert first.id == second.id
        assert await request_count(session, case_id=case.id) == 1
        assert case.status == CaseStatus.M2_CONSULTATION_BOOKED.value
        assert case.assigned_lawyer_id == consultation.lawyer_id
        assert case.next_action == (
            "Ожидайте согласования отмены и условий возможного возврата"
        )
        assert consultation.status == ConsultationStatus.BOOKED.value
        assert consultation.slot_id == slot.id
        assert slot.status == "booked"
        assert slot.consultation_id == consultation.id
        assert payment.status == PaymentStatus.PAID.value
        assert first.new_value["requires_refund_review"] is True


@pytest.mark.asyncio
async def test_foreign_client_cannot_request_paid_cancellation(
    cancellation_request_db,
):
    async with cancellation_request_db() as session:
        user, case, consultation, slot, payment = await seed_paid_booking(
            session,
            suffix=2,
        )

        with pytest.raises(ConsultationNotFoundError, match="не принадлежит"):
            await ConsultationCancellationRequestService(session).request(
                consultation=consultation,
                case=case,
                client_id=user.id + 1,
                actor_id=user.id + 1,
            )

        assert await request_count(session, case_id=case.id) == 0
        assert case.next_action == "Ожидайте консультации в выбранное время"
        assert consultation.slot_id == slot.id
        assert slot.status == "booked"
        assert payment.status == PaymentStatus.PAID.value


@pytest.mark.asyncio
async def test_unpaid_or_completed_status_cannot_create_cancellation_request(
    cancellation_request_db,
):
    async with cancellation_request_db() as session:
        user, case, consultation, slot, _ = await seed_paid_booking(
            session,
            suffix=3,
            consultation_status=ConsultationStatus.DONE.value,
        )

        with pytest.raises(ActiveConsultationConflictError, match="недоступен"):
            await ConsultationCancellationRequestService(session).request(
                consultation=consultation,
                case=case,
                client_id=user.id,
                actor_id=user.id,
            )

        assert await request_count(session, case_id=case.id) == 0
        assert consultation.status == ConsultationStatus.DONE.value
        assert slot.status == "booked"


def test_cancellation_request_router_precedes_legacy_cancel_handler():
    entry = Path("app/bot/screens/consultation_entry.py").read_text(encoding="utf-8")
    bot = Path("app/bot/bot.py").read_text(encoding="utf-8")

    assert entry.index("include_router(cancellation_router)") < entry.index(
        "include_router(hold_router)"
    )
    assert bot.index("consultation_entry.router") < bot.index(
        "consultations.router"
    )
