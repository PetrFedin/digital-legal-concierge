from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.slot_service import SlotService
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
async def protected_hold_db(tmp_path):
    database_path = tmp_path / "expired-hold-payment-protection.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def seed_expired_payment_hold(session, *, suffix: int, payment_status: str):
    now = datetime.now(timezone.utc)
    user = User(
        telegram_id=1_020_000 + suffix,
        telegram_username=f"protected_hold_{suffix}",
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
        case_number=f"PROTECTED-HOLD-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_PAYMENT_PENDING.value,
        title="Юридическая консультация",
        next_action="Оплатите консультацию",
    )
    session.add(case)
    await session.flush()
    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        status=ConsultationStatus.PAYMENT_PENDING.value,
        scheduled_at=now + timedelta(days=2),
        consultation_type="online",
    )
    session.add(consultation)
    await session.flush()
    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=2, minutes=45),
        status="held",
        hold_expires_at=now - timedelta(minutes=1),
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
        provider_payment_id=f"protected-payment-{suffix}",
        payment_url=(
            f"https://pay.example.test/{suffix}"
            if payment_status
            in {
                PaymentStatus.PENDING.value,
                PaymentStatus.WAITING_CONFIRMATION.value,
            }
            else None
        ),
    )
    session.add(payment)
    await session.commit()
    return case, consultation, slot, payment


async def expiry_audit_count(session, *, case_id: int) -> int:
    return int(
        (
            await session.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id == case_id,
                    AuditLog.action == "CONSULTATION_SLOT_HOLD_EXPIRED",
                )
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payment_status",
    [
        PaymentStatus.PENDING.value,
        PaymentStatus.WAITING_CONFIRMATION.value,
        PaymentStatus.PAID.value,
    ],
)
async def test_open_or_paid_payment_extends_expired_hold(
    protected_hold_db,
    payment_status,
):
    async with protected_hold_db() as session:
        case, consultation, slot, _ = await seed_expired_payment_hold(
            session,
            suffix=10 + len(payment_status),
            payment_status=payment_status,
        )
        before = datetime.now(timezone.utc)

        released = await SlotService(session).release_expired_holds()
        await session.commit()

        await session.refresh(case)
        await session.refresh(consultation)
        await session.refresh(slot)
        assert released == 0
        assert slot.status == "held"
        assert slot.consultation_id == consultation.id
        assert slot.hold_expires_at is not None
        normalized_expiry = (
            slot.hold_expires_at.replace(tzinfo=timezone.utc)
            if slot.hold_expires_at.tzinfo is None
            else slot.hold_expires_at.astimezone(timezone.utc)
        )
        assert normalized_expiry >= before + timedelta(minutes=59)
        assert consultation.status == ConsultationStatus.PAYMENT_PENDING.value
        assert consultation.slot_id == slot.id
        assert case.status == CaseStatus.M2_PAYMENT_PENDING.value
        assert case.next_action == (
            "Платёж ожидает подтверждения; выбранное время сохранено"
        )
        assert await expiry_audit_count(session, case_id=case.id) == 0


@pytest.mark.asyncio
async def test_terminal_failed_payment_allows_next_cleanup_cycle(protected_hold_db):
    async with protected_hold_db() as session:
        case, consultation, slot, payment = await seed_expired_payment_hold(
            session,
            suffix=2,
            payment_status=PaymentStatus.WAITING_CONFIRMATION.value,
        )
        service = SlotService(session)
        assert await service.release_expired_holds() == 0
        payment.status = PaymentStatus.FAILED.value
        payment.payment_url = None
        slot.hold_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.flush()

        released = await service.release_expired_holds()
        await session.commit()

        await session.refresh(case)
        await session.refresh(consultation)
        await session.refresh(slot)
        await session.refresh(payment)
        assert released == 1
        assert payment.status == PaymentStatus.FAILED.value
        assert consultation.status == ConsultationStatus.SLOT_PENDING.value
        assert consultation.slot_id is None
        assert consultation.lawyer_id is None
        assert consultation.scheduled_at is None
        assert case.status == CaseStatus.M2_SLOT_PENDING.value
        assert slot.status == "available"
        assert slot.consultation_id is None
        assert slot.held_by_user_id is None
        assert slot.hold_expires_at is None
        assert await expiry_audit_count(session, case_id=case.id) == 1


def test_expiry_cleanup_uses_global_lock_order():
    source = Path("app/domain/consultations/slot_service.py").read_text(
        encoding="utf-8"
    )
    method = source.split("async def release_expired_holds", 1)[1].split(
        "async def get_available_slots",
        1,
    )[0]

    assert method.index("select(Case)") < method.index("select(Lawyer.id)")
    assert method.index("select(Lawyer.id)") < method.index(
        "select(Consultation)"
    )
    assert method.index("select(Consultation)") < method.index(
        "select(ConsultationSlot)"
    )
