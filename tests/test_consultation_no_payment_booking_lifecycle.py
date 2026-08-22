from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.domain.consultations.consultation_no_payment_booking import (
    ConsultationNoPaymentBookingService,
)
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.payment_service import PaymentService
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


async def _seed_booking(session_factory, *, payment_status: PaymentStatus):
    starts_at = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    async with session_factory() as db:
        user = User(telegram_id=990000301, full_name="No-payment Client")
        lawyer = Lawyer(full_name="No-payment Lawyer", telegram_id=990000302)
        db.add_all([user, lawyer])
        await db.flush()

        case = Case(
            case_number="NO-PAY-M2-1",
            client_id=user.id,
            route=RouteCode.M2.value,
            status=CaseStatus.M2_PAYMENT_PENDING.value,
            title="Юридическая консультация",
        )
        db.add(case)
        await db.flush()

        consultation = Consultation(
            case_id=case.id,
            status=ConsultationStatus.PAYMENT_PENDING.value,
            client_description=(
                "Нужно проверить сроки передачи квартиры по ДДУ и порядок дальнейших действий."
            ),
        )
        db.add(consultation)
        await db.flush()

        slot = ConsultationSlot(
            lawyer_id=lawyer.id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            status="held",
            hold_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            held_by_user_id=user.id,
            consultation_id=consultation.id,
        )
        db.add(slot)
        await db.flush()

        consultation.slot_id = slot.id
        consultation.lawyer_id = lawyer.id
        consultation.scheduled_at = starts_at

        reservation_key = PaymentService.consultation_reservation_key(
            consultation.id,
            slot.id,
        )
        payment = Payment(
            case_id=case.id,
            payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
            title="Оплата консультации",
            amount=Decimal("5000.00"),
            currency="RUB",
            status=payment_status.value,
            provider="fake",
            provider_payment_id="provider-no-pay-1",
            payment_url="https://payments.invalid/no-pay-1",
            reservation_key=reservation_key,
        )
        db.add(payment)
        await db.commit()
        return {
            "case_id": int(case.id),
            "consultation_id": int(consultation.id),
            "slot_id": int(slot.id),
            "payment_id": int(payment.id),
            "user_id": int(user.id),
            "starts_at": starts_at,
            "reservation_key": reservation_key,
        }


def test_no_payment_booking_expires_exact_active_link_and_books_atomically(monkeypatch):
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        monkeypatch.setattr(settings, "app_env", "test")
        monkeypatch.setattr(settings, "payment_provider", "disabled")
        monkeypatch.setattr(settings, "demo_mode", False)

        captured_notifications: list[dict] = []

        async def capture_emit(self, **kwargs):
            captured_notifications.append(dict(kwargs))
            return []

        monkeypatch.setattr(NotificationEngine, "emit", capture_emit)
        seeded = await _seed_booking(
            session_factory,
            payment_status=PaymentStatus.PENDING,
        )

        async with session_factory() as db:
            case = await db.get(Case, seeded["case_id"])
            assert case is not None
            consultation = await ConsultationNoPaymentBookingService(db).confirm(
                case=case,
                client_id=seeded["user_id"],
            )
            assert consultation.status == ConsultationStatus.BOOKED
            await db.commit()

        async with session_factory() as db:
            payment = await db.get(Payment, seeded["payment_id"])
            consultation = await db.get(Consultation, seeded["consultation_id"])
            slot = await db.get(ConsultationSlot, seeded["slot_id"])
            case = await db.get(Case, seeded["case_id"])
            assert payment is not None
            assert consultation is not None
            assert slot is not None
            assert case is not None

            assert payment.status == PaymentStatus.EXPIRED
            assert payment.expired_at is not None
            assert payment.paid_at is None
            assert consultation.status == ConsultationStatus.BOOKED
            assert slot.status == "booked"
            assert case.status == CaseStatus.M2_CONSULTATION_BOOKED

            event = (
                await db.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.entity_type == "case",
                        AuditLog.entity_id == seeded["case_id"],
                        AuditLog.action
                        == "CONSULTATION_ONLINE_PAYMENT_EXPIRED_AFTER_NO_PAYMENT_BOOKING",
                    )
                    .order_by(AuditLog.id.desc())
                )
            ).scalars().first()
            assert event is not None
            assert event.old_value["status"] == PaymentStatus.PENDING.value
            assert event.new_value["status"] == PaymentStatus.EXPIRED.value
            assert event.new_value["expired_at"]
            assert event.new_value["reservation_key"] == seeded["reservation_key"]

        assert len(captured_notifications) == 1
        notification = captured_notifications[0]
        assert notification["event_code"] == "M2_CONSULTATION_BOOKED"
        assert notification["case_id"] == seeded["case_id"]
        assert notification["user_id"] == seeded["user_id"]
        assert notification["payload"]["case_number"] == "NO-PAY-M2-1"
        assert notification["payload"]["date"] == "25.08.2026 15:00 МСК"
        assert notification["dedupe_key"] == f'{seeded["reservation_key"]}:booked'

        await engine.dispose()

    asyncio.run(scenario())


def test_no_payment_booking_fails_closed_when_money_was_already_received(monkeypatch):
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        monkeypatch.setattr(settings, "app_env", "test")
        monkeypatch.setattr(settings, "payment_provider", "disabled")
        monkeypatch.setattr(settings, "demo_mode", False)
        seeded = await _seed_booking(
            session_factory,
            payment_status=PaymentStatus.PAID,
        )

        async with session_factory() as db:
            case = await db.get(Case, seeded["case_id"])
            assert case is not None
            with pytest.raises(ValueError, match="поступление денег или возврат"):
                await ConsultationNoPaymentBookingService(db).confirm(
                    case=case,
                    client_id=seeded["user_id"],
                )
            await db.rollback()

        async with session_factory() as db:
            payment = await db.get(Payment, seeded["payment_id"])
            consultation = await db.get(Consultation, seeded["consultation_id"])
            slot = await db.get(ConsultationSlot, seeded["slot_id"])
            case = await db.get(Case, seeded["case_id"])
            assert payment is not None
            assert consultation is not None
            assert slot is not None
            assert case is not None

            assert payment.status == PaymentStatus.PAID
            assert payment.paid_at is not None
            assert payment.expired_at is None
            assert consultation.status == ConsultationStatus.PAYMENT_PENDING
            assert slot.status == "held"
            assert case.status == CaseStatus.M2_PAYMENT_PENDING

            wrong_expiry = (
                await db.execute(
                    select(AuditLog).where(
                        AuditLog.entity_type == "case",
                        AuditLog.entity_id == seeded["case_id"],
                        AuditLog.action
                        == "CONSULTATION_ONLINE_PAYMENT_EXPIRED_AFTER_NO_PAYMENT_BOOKING",
                    )
                )
            ).scalars().first()
            assert wrong_expiry is None

        await engine.dispose()

    asyncio.run(scenario())
