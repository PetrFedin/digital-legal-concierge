from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.consultation_service import (
    ConsultationNotFoundError,
    ConsultationSlotError,
)
from app.domain.consultations.reschedule_service import (
    ConsultationRescheduleService,
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
async def reschedule_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'consultation-reschedule.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def seed_reschedule(session, *, suffix: int = 1):
    starts_at = datetime.now(timezone.utc) + timedelta(days=2)
    user = User(
        telegram_id=980_000 + suffix,
        full_name=f"Клиент переноса {suffix}",
    )
    lawyer = Lawyer(
        full_name=f"Юрист переноса {suffix}",
        is_active=True,
    )
    other_lawyer = Lawyer(
        full_name=f"Другой юрист {suffix}",
        is_active=True,
    )
    session.add_all([user, lawyer, other_lawyer])
    await session.flush()

    case = Case(
        case_number=f"RESCHEDULE-{suffix}",
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

    old_slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        status="booked",
        held_by_user_id=user.id,
        consultation_id=consultation.id,
    )
    replacement = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at + timedelta(days=1),
        ends_at=starts_at + timedelta(days=1, hours=1),
        status="available",
    )
    other_lawyer_slot = ConsultationSlot(
        lawyer_id=other_lawyer.id,
        starts_at=starts_at + timedelta(days=2),
        ends_at=starts_at + timedelta(days=2, hours=1),
        status="available",
    )
    session.add_all([old_slot, replacement, other_lawyer_slot])
    await session.flush()
    consultation.slot_id = old_slot.id

    payment = Payment(
        case_id=case.id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT.value,
        title="Юридическая консультация",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=PaymentStatus.PAID.value,
        provider="fake",
        provider_payment_id=f"reschedule-payment-{suffix}",
    )
    session.add(payment)
    await session.commit()
    return (
        user,
        case,
        consultation,
        old_slot,
        replacement,
        other_lawyer_slot,
        payment,
    )


async def reschedule_audit_count(session, case_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.entity_type == "case",
                AuditLog.entity_id == case_id,
                AuditLog.action == "CONSULTATION_RESCHEDULED",
            )
        )
        or 0
    )


@pytest.mark.asyncio
async def test_reschedule_atomically_replaces_slot_and_preserves_payment(
    reschedule_db,
):
    async with reschedule_db() as session:
        (
            user,
            case,
            consultation,
            old_slot,
            replacement,
            _,
            payment,
        ) = await seed_reschedule(session)

        updated, new_slot = await ConsultationRescheduleService(session).reschedule(
            consultation=consultation,
            case=case,
            client_id=user.id,
            new_slot_id=replacement.id,
            actor_id=user.id,
        )
        await session.commit()

        await session.refresh(old_slot)
        await session.refresh(replacement)
        await session.refresh(payment)
        assert updated.slot_id == replacement.id
        assert updated.scheduled_at == replacement.starts_at
        assert updated.status == ConsultationStatus.BOOKED.value
        assert new_slot.id == replacement.id
        assert old_slot.status == "available"
        assert old_slot.consultation_id is None
        assert replacement.status == "booked"
        assert replacement.consultation_id == consultation.id
        assert replacement.held_by_user_id == user.id
        assert payment.status == PaymentStatus.PAID.value
        assert await reschedule_audit_count(session, case.id) == 1


@pytest.mark.asyncio
async def test_failed_reschedule_preserves_original_booking(reschedule_db):
    async with reschedule_db() as session:
        (
            user,
            case,
            consultation,
            old_slot,
            replacement,
            _,
            payment,
        ) = await seed_reschedule(session, suffix=2)
        original_slot_id = consultation.slot_id
        original_scheduled_at = consultation.scheduled_at
        replacement.status = "booked"
        await session.commit()

        with pytest.raises(ConsultationSlotError, match="прежняя запись сохранена"):
            await ConsultationRescheduleService(session).reschedule(
                consultation=consultation,
                case=case,
                client_id=user.id,
                new_slot_id=replacement.id,
                actor_id=user.id,
            )

        await session.rollback()
        await session.refresh(consultation)
        await session.refresh(old_slot)
        await session.refresh(replacement)
        await session.refresh(payment)
        assert consultation.slot_id == original_slot_id
        assert consultation.scheduled_at == original_scheduled_at
        assert consultation.status == ConsultationStatus.BOOKED.value
        assert old_slot.status == "booked"
        assert old_slot.consultation_id == consultation.id
        assert replacement.status == "booked"
        assert payment.status == PaymentStatus.PAID.value
        assert await reschedule_audit_count(session, case.id) == 0


@pytest.mark.asyncio
async def test_repeated_reschedule_to_same_slot_is_idempotent(reschedule_db):
    async with reschedule_db() as session:
        user, case, consultation, old_slot, _, _, payment = await seed_reschedule(
            session,
            suffix=3,
        )

        first, first_slot = await ConsultationRescheduleService(session).reschedule(
            consultation=consultation,
            case=case,
            client_id=user.id,
            new_slot_id=old_slot.id,
            actor_id=user.id,
        )
        second, second_slot = await ConsultationRescheduleService(session).reschedule(
            consultation=consultation,
            case=case,
            client_id=user.id,
            new_slot_id=old_slot.id,
            actor_id=user.id,
        )
        await session.commit()

        await session.refresh(payment)
        assert first.id == second.id == consultation.id
        assert first_slot.id == second_slot.id == old_slot.id
        assert consultation.slot_id == old_slot.id
        assert old_slot.status == "booked"
        assert payment.status == PaymentStatus.PAID.value
        assert await reschedule_audit_count(session, case.id) == 0


@pytest.mark.asyncio
async def test_reschedule_rejects_slot_of_another_lawyer(reschedule_db):
    async with reschedule_db() as session:
        (
            user,
            case,
            consultation,
            old_slot,
            _,
            other_lawyer_slot,
            payment,
        ) = await seed_reschedule(session, suffix=4)

        with pytest.raises(ConsultationSlotError, match="другому юристу"):
            await ConsultationRescheduleService(session).reschedule(
                consultation=consultation,
                case=case,
                client_id=user.id,
                new_slot_id=other_lawyer_slot.id,
                actor_id=user.id,
            )

        await session.rollback()
        await session.refresh(consultation)
        await session.refresh(old_slot)
        await session.refresh(other_lawyer_slot)
        await session.refresh(payment)
        assert consultation.slot_id == old_slot.id
        assert old_slot.status == "booked"
        assert other_lawyer_slot.status == "available"
        assert payment.status == PaymentStatus.PAID.value
        assert await reschedule_audit_count(session, case.id) == 0


@pytest.mark.asyncio
async def test_foreign_client_cannot_reschedule_consultation(reschedule_db):
    async with reschedule_db() as session:
        (
            user,
            case,
            consultation,
            old_slot,
            replacement,
            _,
            _,
        ) = await seed_reschedule(session, suffix=5)

        with pytest.raises(ConsultationNotFoundError):
            await ConsultationRescheduleService(session).reschedule(
                consultation=consultation,
                case=case,
                client_id=user.id + 1000,
                new_slot_id=replacement.id,
                actor_id=user.id + 1000,
            )

        assert consultation.slot_id == old_slot.id
        assert old_slot.status == "booked"
        assert replacement.status == "available"
        assert await reschedule_audit_count(session, case.id) == 0
