from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.consultation_service import (
    ActiveConsultationConflictError,
    ConsultationService,
    ConsultationSlotError,
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
async def cancellation_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'consultation-cancellation.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def seed_booked_consultation(session, *, suffix: int = 1):
    starts_at = datetime.now(timezone.utc) + timedelta(days=2)
    user = User(
        telegram_id=970_000 + suffix,
        full_name=f"Клиент отмены {suffix}",
    )
    lawyer = Lawyer(
        full_name=f"Юрист отмены {suffix}",
        is_active=True,
    )
    session.add_all([user, lawyer])
    await session.flush()

    case = Case(
        case_number=f"CANCEL-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_CONSULTATION_BOOKED.value,
        title="Юридическая консультация",
        next_action="Ожидайте консультации",
        assigned_lawyer_id=lawyer.id,
    )
    session.add(case)
    await session.flush()

    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        status=ConsultationStatus.BOOKED.value,
        scheduled_at=starts_at,
    )
    session.add(consultation)
    await session.flush()

    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
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
        title="Юридическая консультация",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=PaymentStatus.PAID.value,
        provider="fake",
        provider_payment_id=f"cancel-payment-{suffix}",
    )
    session.add(payment)
    await session.commit()
    return user, case, consultation, slot, payment


async def cancellation_audit_count(session, case_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.entity_type == "case",
                AuditLog.entity_id == case_id,
                AuditLog.action == "CONSULTATION_CANCELLED",
            )
        )
        or 0
    )


@pytest.mark.asyncio
async def test_cancellation_releases_only_linked_slot_and_preserves_payment(
    cancellation_db,
):
    async with cancellation_db() as session:
        user, case, consultation, slot, payment = await seed_booked_consultation(
            session
        )

        cancelled = await ConsultationService(session).cancel(
            consultation=consultation,
            case=case,
            actor_type="client",
            actor_id=user.id,
            comment="Клиент подтвердил отмену",
        )
        await session.commit()

        await session.refresh(slot)
        await session.refresh(payment)
        assert cancelled.status == ConsultationStatus.CANCELLED.value
        assert cancelled.slot_id is None
        assert slot.status == "available"
        assert slot.consultation_id is None
        assert slot.held_by_user_id is None
        assert case.status == CaseStatus.M2_CLOSED.value
        assert case.next_action == "Консультация отменена"
        assert payment.status == PaymentStatus.PAID.value
        assert await cancellation_audit_count(session, case.id) == 1


@pytest.mark.asyncio
async def test_repeated_cancellation_is_idempotent(cancellation_db):
    async with cancellation_db() as session:
        user, case, consultation, _, _ = await seed_booked_consultation(session, suffix=2)
        service = ConsultationService(session)

        first = await service.cancel(
            consultation=consultation,
            case=case,
            actor_type="client",
            actor_id=user.id,
            comment="Первая отмена",
        )
        second = await service.cancel(
            consultation=consultation,
            case=case,
            actor_type="client",
            actor_id=user.id,
            comment="Повторная отмена",
        )
        await session.commit()

        assert first.id == second.id == consultation.id
        assert second.status == ConsultationStatus.CANCELLED.value
        assert await cancellation_audit_count(session, case.id) == 1


@pytest.mark.asyncio
async def test_cancellation_rejects_slot_owned_by_another_consultation(
    cancellation_db,
):
    async with cancellation_db() as session:
        user, case, consultation, slot, payment = await seed_booked_consultation(
            session,
            suffix=3,
        )
        other = Consultation(
            case_id=case.id,
            lawyer_id=consultation.lawyer_id,
            status=ConsultationStatus.BOOKED.value,
            scheduled_at=consultation.scheduled_at,
        )
        session.add(other)
        await session.flush()
        slot.consultation_id = other.id
        await session.commit()

        with pytest.raises(ConsultationSlotError, match="другой записи"):
            await ConsultationService(session).cancel(
                consultation=consultation,
                case=case,
                actor_type="client",
                actor_id=user.id,
                comment="Некорректная отмена",
            )

        await session.rollback()
        await session.refresh(slot)
        await session.refresh(payment)
        assert consultation.status == ConsultationStatus.BOOKED.value
        assert consultation.slot_id == slot.id
        assert slot.status == "booked"
        assert slot.consultation_id == other.id
        assert payment.status == PaymentStatus.PAID.value
        assert await cancellation_audit_count(session, case.id) == 0


@pytest.mark.asyncio
async def test_completed_consultation_cannot_be_cancelled(cancellation_db):
    async with cancellation_db() as session:
        user, case, consultation, slot, _ = await seed_booked_consultation(
            session,
            suffix=4,
        )
        consultation.status = ConsultationStatus.DONE.value
        case.status = CaseStatus.M2_CONSULTATION_DONE.value
        await session.commit()

        with pytest.raises(ActiveConsultationConflictError, match="нельзя отменить"):
            await ConsultationService(session).cancel(
                consultation=consultation,
                case=case,
                actor_type="client",
                actor_id=user.id,
                comment="Недопустимая отмена",
            )

        await session.rollback()
        await session.refresh(slot)
        assert consultation.status == ConsultationStatus.DONE.value
        assert slot.status == "booked"
        assert await cancellation_audit_count(session, case.id) == 0
