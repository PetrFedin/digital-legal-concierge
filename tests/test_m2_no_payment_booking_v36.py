from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.domain.consultations.consultation_no_payment_booking as booking_module
from app.domain.consultations.consultation_no_payment_booking import (
    ConsultationNoPaymentBookingService,
)
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.user import User


@asynccontextmanager
async def database(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'm2-no-payment-v36.db'}"
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield factory
    finally:
        await engine.dispose()


async def seed_reserved_consultation(session):
    user = User(telegram_id=983204, full_name="Клиент Без Онлайн Оплаты")
    lawyer = Lawyer(full_name="Юрист Консультации", is_active=True)
    session.add_all([user, lawyer])
    await session.flush()

    case = Case(
        case_number="M2-NOPAY-V36",
        client_id=user.id,
        route=RouteCode.M2,
        status=CaseStatus.M2_PAYMENT_PENDING,
        title="Консультация без онлайн-оплаты",
    )
    session.add(case)
    await session.flush()

    consultation = Consultation(
        case_id=case.id,
        status=ConsultationStatus.PAYMENT_PENDING,
        client_description=(
            "Нужна консультация по договору и оценка конкретного дальнейшего действия."
        ),
        subject_type="new_or_other",
    )
    session.add(consultation)
    await session.flush()

    starts_at = datetime.now(timezone.utc) + timedelta(days=2)
    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        status="held",
        hold_expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        held_by_user_id=user.id,
        consultation_id=consultation.id,
    )
    session.add(slot)
    await session.flush()
    consultation.slot_id = slot.id
    await session.flush()
    return user, lawyer, case, consultation, slot


@pytest.mark.asyncio
async def test_disabled_mode_books_reserved_m2_without_creating_fake_payment(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(booking_module, "payments_disabled", lambda: True)

    async with database(tmp_path) as factory:
        async with factory() as session:
            user, lawyer, case, consultation, slot = await seed_reserved_consultation(
                session
            )

            booked = await ConsultationNoPaymentBookingService(session).confirm(
                case=case,
                client_id=user.id,
            )
            await session.commit()
            await session.refresh(case)
            await session.refresh(booked)
            await session.refresh(slot)

            assert case.status == CaseStatus.M2_CONSULTATION_BOOKED
            assert booked.status == ConsultationStatus.BOOKED
            assert booked.lawyer_id == lawyer.id
            assert booked.slot_id == slot.id
            assert booked.scheduled_at is not None
            assert slot.status == "booked"
            assert slot.held_by_user_id is None
            assert slot.hold_expires_at is None
            assert slot.consultation_id == booked.id

            payments = (
                await session.execute(select(Payment).where(Payment.case_id == case.id))
            ).scalars().all()
            assert payments == []

            audits = (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.entity_type == "case")
                    .where(AuditLog.entity_id == case.id)
                    .order_by(AuditLog.id.asc())
                )
            ).scalars().all()
            no_payment_events = [
                event
                for event in audits
                if event.action == "CONSULTATION_BOOKED_WITHOUT_PAYMENT"
            ]
            assert len(no_payment_events) == 1
            assert no_payment_events[0].actor_type == "client"
            assert no_payment_events[0].actor_id == user.id
            assert (no_payment_events[0].new_value or {})["payment_required"] is False

            notifications = (
                await session.execute(
                    select(Notification).where(
                        Notification.case_id == case.id,
                        Notification.event_code == "M2_CONSULTATION_BOOKED",
                    )
                )
            ).scalars().all()
            assert len(notifications) == 1

            with pytest.raises(ValueError, match="Консультация уже изменилась"):
                await ConsultationNoPaymentBookingService(session).confirm(
                    case=case,
                    client_id=user.id,
                )
            await session.rollback()

            notifications_after_retry = (
                await session.execute(
                    select(Notification).where(
                        Notification.case_id == case.id,
                        Notification.event_code == "M2_CONSULTATION_BOOKED",
                    )
                )
            ).scalars().all()
            assert len(notifications_after_retry) == 1


@pytest.mark.asyncio
async def test_no_payment_booking_is_blocked_when_online_provider_is_enabled(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(booking_module, "payments_disabled", lambda: False)

    async with database(tmp_path) as factory:
        async with factory() as session:
            user, _lawyer, case, consultation, slot = await seed_reserved_consultation(
                session
            )

            with pytest.raises(ValueError, match="только когда онлайн-оплата отключена"):
                await ConsultationNoPaymentBookingService(session).confirm(
                    case=case,
                    client_id=user.id,
                )
            await session.rollback()
            await session.refresh(case)
            await session.refresh(consultation)
            await session.refresh(slot)

            assert case.status == CaseStatus.M2_PAYMENT_PENDING
            assert consultation.status == ConsultationStatus.PAYMENT_PENDING
            assert slot.status == "held"
            assert (
                await session.execute(select(Payment).where(Payment.case_id == case.id))
            ).scalars().all() == []


def test_telegram_consult_pay_uses_no_payment_domain_path_before_payment_provider():
    source = Path("app/bot/screens/payments.py").read_text(encoding="utf-8")
    handler_start = source.index('@router.callback_query(lambda c: c.data == "consult_pay")')
    handler_end = source.index("async def _show_missing_m1_payment_case", handler_start)
    handler = source[handler_start:handler_end]

    assert "if not payments_disabled():" in handler
    assert "ConsultationNoPaymentBookingService(db).confirm" in handler
    assert "Дополнительный платёж не создавался" in handler
    assert "await db.commit()" in handler
    assert "await db.rollback()" in handler
    assert "PaymentService" not in handler
    assert "create_payment_link" not in handler
    assert "M2_CONSULTATION_BOOKED" in handler
