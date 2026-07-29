from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.consultation_service import (
    ActiveConsultationConflictError,
    ConsultationNotFoundError,
    ConsultationSlotError,
)
from app.domain.consultations.reservation_service import (
    ConsultationReservationService,
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
async def reservation_db(tmp_path):
    database_path = tmp_path / "consultation-reservation-release.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        yield session_factory
    finally:
        await engine.dispose()


async def seed_reservation(session, *, suffix: int = 1):
    starts_at = datetime.now(timezone.utc) + timedelta(days=2)
    user = User(
        telegram_id=980_000 + suffix,
        telegram_username=f"reservation_{suffix}",
        full_name=f"Клиент {suffix}",
    )
    lawyer = Lawyer(
        full_name=f"Юрист {suffix}",
        specialization="Споры по ДДУ",
        workload_limit=10,
        is_active=True,
    )
    session.add_all([user, lawyer])
    await session.flush()

    case = Case(
        case_number=f"RESERVATION-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_PAYMENT_PENDING.value,
        title="Юридическая консультация",
        next_action="Перейдите к оплате консультации",
    )
    session.add(case)
    await session.flush()

    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        status=ConsultationStatus.SLOT_RESERVED.value,
        scheduled_at=starts_at,
        consultation_type="online",
    )
    session.add(consultation)
    await session.flush()

    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=45),
        status="held",
        hold_expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        held_by_user_id=user.id,
        consultation_id=consultation.id,
    )
    session.add(slot)
    await session.flush()
    consultation.slot_id = slot.id
    await session.flush()
    return user, case, consultation, slot


async def add_payment(session, *, case: Case, status: str) -> Payment:
    payment = Payment(
        case_id=case.id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT.value,
        title="Оплата консультации",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=status,
    )
    session.add(payment)
    await session.flush()
    return payment


async def audit_count(session, *, case_id: int) -> int:
    return int(
        (
            await session.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id == case_id,
                    AuditLog.action == "CONSULTATION_SLOT_RELEASED_BY_CLIENT",
                )
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_client_releases_hold_before_payment_atomically(reservation_db):
    async with reservation_db() as session:
        user, case, consultation, slot = await seed_reservation(session)

        released = await ConsultationReservationService(
            session
        ).release_before_payment(
            consultation=consultation,
            case=case,
            client_id=user.id,
            actor_id=user.id,
        )
        await session.commit()

        await session.refresh(case)
        await session.refresh(released)
        await session.refresh(slot)
        assert released.status == ConsultationStatus.SLOT_PENDING.value
        assert released.slot_id is None
        assert released.lawyer_id is None
        assert released.scheduled_at is None
        assert case.status == CaseStatus.M2_SLOT_PENDING.value
        assert case.next_action == "Выберите удобное время консультации"
        assert slot.status == "available"
        assert slot.consultation_id is None
        assert slot.held_by_user_id is None
        assert slot.hold_expires_at is None
        assert await audit_count(session, case_id=case.id) == 1


@pytest.mark.asyncio
async def test_repeated_release_is_idempotent(reservation_db):
    async with reservation_db() as session:
        user, case, consultation, _ = await seed_reservation(session, suffix=2)
        service = ConsultationReservationService(session)

        first = await service.release_before_payment(
            consultation=consultation,
            case=case,
            client_id=user.id,
            actor_id=user.id,
        )
        second = await service.release_before_payment(
            consultation=consultation,
            case=case,
            client_id=user.id,
            actor_id=user.id,
        )

        assert first.id == second.id == consultation.id
        assert await audit_count(session, case_id=case.id) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payment_status",
    [
        PaymentStatus.PENDING.value,
        PaymentStatus.WAITING_CONFIRMATION.value,
        PaymentStatus.PAID.value,
    ],
)
async def test_open_or_paid_payment_blocks_release(reservation_db, payment_status):
    async with reservation_db() as session:
        user, case, consultation, slot = await seed_reservation(
            session,
            suffix=10 + len(payment_status),
        )
        await add_payment(session, case=case, status=payment_status)

        with pytest.raises(
            ActiveConsultationConflictError,
            match="Платёж уже создан или подтверждён",
        ):
            await ConsultationReservationService(session).release_before_payment(
                consultation=consultation,
                case=case,
                client_id=user.id,
                actor_id=user.id,
            )

        await session.refresh(consultation)
        await session.refresh(slot)
        assert consultation.status == ConsultationStatus.SLOT_RESERVED.value
        assert consultation.slot_id == slot.id
        assert slot.status == "held"
        assert await audit_count(session, case_id=case.id) == 0


@pytest.mark.asyncio
async def test_failed_payment_does_not_block_release(reservation_db):
    async with reservation_db() as session:
        user, case, consultation, slot = await seed_reservation(session, suffix=4)
        await add_payment(session, case=case, status=PaymentStatus.FAILED.value)

        await ConsultationReservationService(session).release_before_payment(
            consultation=consultation,
            case=case,
            client_id=user.id,
            actor_id=user.id,
        )

        await session.refresh(slot)
        assert consultation.status == ConsultationStatus.SLOT_PENDING.value
        assert slot.status == "available"
        assert await audit_count(session, case_id=case.id) == 1


@pytest.mark.asyncio
async def test_foreign_client_cannot_release_reservation(reservation_db):
    async with reservation_db() as session:
        user, case, consultation, slot = await seed_reservation(session, suffix=5)

        with pytest.raises(ConsultationNotFoundError, match="не принадлежит"):
            await ConsultationReservationService(session).release_before_payment(
                consultation=consultation,
                case=case,
                client_id=user.id + 1,
                actor_id=user.id + 1,
            )

        assert consultation.slot_id == slot.id
        assert slot.status == "held"
        assert await audit_count(session, case_id=case.id) == 0


@pytest.mark.asyncio
async def test_mismatched_slot_blocks_release_without_partial_changes(reservation_db):
    async with reservation_db() as session:
        user, case, consultation, slot = await seed_reservation(session, suffix=6)
        slot.consultation_id = None
        await session.flush()

        with pytest.raises(ConsultationSlotError, match="не найден"):
            await ConsultationReservationService(session).release_before_payment(
                consultation=consultation,
                case=case,
                client_id=user.id,
                actor_id=user.id,
            )

        await session.refresh(consultation)
        await session.refresh(slot)
        assert consultation.status == ConsultationStatus.SLOT_RESERVED.value
        assert consultation.slot_id == slot.id
        assert slot.status == "held"
        assert await audit_count(session, case_id=case.id) == 0
