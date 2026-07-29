from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.consultation_service import ConsultationService
from app.domain.consultations.slot_service import SlotUnavailableError
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.user import User


async def create_database(tmp_path, name: str):
    database_path = tmp_path / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, session_factory


async def create_reschedule_context(session, *, suffix: int):
    old_lawyer = Lawyer(
        full_name=f"Первый юрист {suffix}",
        is_active=True,
        workload_limit=10,
    )
    new_lawyer = Lawyer(
        full_name=f"Новый юрист {suffix}",
        is_active=True,
        workload_limit=10,
    )
    user = User(
        telegram_id=810000 + suffix,
        full_name=f"Клиент переноса {suffix}",
    )
    session.add_all([old_lawyer, new_lawyer, user])
    await session.flush()

    case = Case(
        case_number=f"RESCHEDULE-{suffix}",
        client_id=user.id,
        route="M2",
        status=CaseStatus.M2_CONSULTATION_BOOKED,
        title="Оплаченная консультация",
    )
    session.add(case)
    await session.flush()

    consultation = Consultation(
        case_id=case.id,
        lawyer_id=old_lawyer.id,
        status=ConsultationStatus.BOOKED,
    )
    session.add(consultation)
    await session.flush()

    old_starts_at = datetime.now(timezone.utc) + timedelta(days=1)
    new_starts_at = datetime.now(timezone.utc) + timedelta(days=2)
    old_slot = ConsultationSlot(
        lawyer_id=old_lawyer.id,
        starts_at=old_starts_at,
        ends_at=old_starts_at + timedelta(hours=1),
        status="booked",
        held_by_user_id=user.id,
        consultation_id=consultation.id,
    )
    new_slot = ConsultationSlot(
        lawyer_id=new_lawyer.id,
        starts_at=new_starts_at,
        ends_at=new_starts_at + timedelta(hours=1),
        status="available",
    )
    session.add_all([old_slot, new_slot])
    await session.flush()

    consultation.slot_id = old_slot.id
    consultation.scheduled_at = old_slot.starts_at
    payment = Payment(
        case_id=case.id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
        title="Оплата консультации",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=PaymentStatus.PAID,
        provider="fake",
        provider_payment_id=f"reschedule-paid-{suffix}",
        reservation_key=PaymentService.consultation_reservation_key(
            consultation.id,
            old_slot.id,
        ),
    )
    session.add(payment)
    await session.flush()
    return {
        "old_lawyer": old_lawyer,
        "new_lawyer": new_lawyer,
        "user": user,
        "case": case,
        "consultation": consultation,
        "old_slot": old_slot,
        "new_slot": new_slot,
        "payment": payment,
    }


@pytest.mark.asyncio
async def test_paid_consultation_reschedules_without_new_payment(tmp_path):
    engine, session_factory = await create_database(
        tmp_path,
        "reschedule-success.db",
    )

    async with session_factory() as session:
        context = await create_reschedule_context(session, suffix=1)
        await session.commit()

        consultation, new_slot = await ConsultationService(
            session
        ).reschedule_booked(
            consultation=context["consultation"],
            case=context["case"],
            client_id=context["user"].id,
            new_slot_id=context["new_slot"].id,
        )
        await session.commit()

        old_slot = await session.get(
            ConsultationSlot,
            context["old_slot"].id,
        )
        await session.refresh(new_slot)
        await session.refresh(consultation)
        payments = (
            await session.execute(
                select(Payment).where(Payment.case_id == context["case"].id)
            )
        ).scalars().all()

        assert old_slot.status == "available"
        assert old_slot.consultation_id is None
        assert old_slot.held_by_user_id is None
        assert new_slot.status == "booked"
        assert new_slot.consultation_id == consultation.id
        assert new_slot.held_by_user_id == context["user"].id
        assert consultation.status == ConsultationStatus.BOOKED
        assert consultation.slot_id == new_slot.id
        assert consultation.lawyer_id == context["new_lawyer"].id
        assert consultation.scheduled_at == new_slot.starts_at
        assert len(payments) == 1
        assert payments[0].status == PaymentStatus.PAID

        audit_count = (
            await session.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_id == context["case"].id,
                    AuditLog.action == "CONSULTATION_RESCHEDULED",
                )
            )
        ).scalar_one()
        notification_count = (
            await session.execute(
                select(func.count(Notification.id)).where(
                    Notification.case_id == context["case"].id,
                    Notification.event_code == "CONSULTATION_RESCHEDULED",
                )
            )
        ).scalar_one()
        assert audit_count == 1
        assert notification_count == 3

    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_reschedule_rolls_back_and_keeps_old_slot(tmp_path):
    engine, session_factory = await create_database(
        tmp_path,
        "reschedule-rollback.db",
    )

    async with session_factory() as session:
        context = await create_reschedule_context(session, suffix=2)
        context["new_slot"].status = "booked"
        await session.commit()
        old_slot_id = context["old_slot"].id
        new_slot_id = context["new_slot"].id
        consultation_id = context["consultation"].id

        with pytest.raises(SlotUnavailableError):
            await ConsultationService(session).reschedule_booked(
                consultation=context["consultation"],
                case=context["case"],
                client_id=context["user"].id,
                new_slot_id=new_slot_id,
            )
        await session.rollback()

        old_slot = await session.get(ConsultationSlot, old_slot_id)
        new_slot = await session.get(ConsultationSlot, new_slot_id)
        consultation = await session.get(Consultation, consultation_id)
        assert old_slot.status == "booked"
        assert old_slot.consultation_id == consultation.id
        assert new_slot.status == "booked"
        assert consultation.slot_id == old_slot.id
        assert consultation.status == ConsultationStatus.BOOKED

    await engine.dispose()


@pytest.mark.asyncio
async def test_started_consultation_cannot_be_rescheduled(tmp_path):
    engine, session_factory = await create_database(
        tmp_path,
        "reschedule-started.db",
    )

    async with session_factory() as session:
        context = await create_reschedule_context(session, suffix=3)
        context["old_slot"].starts_at = datetime.now(timezone.utc) - timedelta(
            minutes=30
        )
        context["old_slot"].ends_at = datetime.now(timezone.utc) + timedelta(
            minutes=30
        )
        context["consultation"].scheduled_at = context["old_slot"].starts_at
        await session.commit()

        with pytest.raises(ValueError, match="уже началась"):
            await ConsultationService(session).reschedule_booked(
                consultation=context["consultation"],
                case=context["case"],
                client_id=context["user"].id,
                new_slot_id=context["new_slot"].id,
            )
        await session.rollback()

        old_slot = await session.get(
            ConsultationSlot,
            context["old_slot"].id,
        )
        consultation = await session.get(
            Consultation,
            context["consultation"].id,
        )
        assert old_slot.status == "booked"
        assert consultation.slot_id == old_slot.id

    await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_paid_webhook_after_reschedule_stays_paid(tmp_path):
    engine, session_factory = await create_database(
        tmp_path,
        "reschedule-webhook.db",
    )

    async with session_factory() as session:
        context = await create_reschedule_context(session, suffix=4)
        await session.commit()

        await ConsultationService(session).reschedule_booked(
            consultation=context["consultation"],
            case=context["case"],
            client_id=context["user"].id,
            new_slot_id=context["new_slot"].id,
        )
        await session.commit()

        await PaymentWebhookService(session).process_successful_payment(
            payment=context["payment"],
            case=context["case"],
            provider_payload={"event": "duplicate_after_reschedule"},
        )
        await session.commit()

        payment = await session.get(Payment, context["payment"].id)
        consultation = await session.get(
            Consultation,
            context["consultation"].id,
        )
        review_count = (
            await session.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_id == context["case"].id,
                    AuditLog.action == "CONSULTATION_PAYMENT_REVIEW_REQUIRED",
                )
            )
        ).scalar_one()

        assert payment.status == PaymentStatus.PAID
        assert consultation.status == ConsultationStatus.BOOKED
        assert consultation.slot_id == context["new_slot"].id
        assert review_count == 0

    await engine.dispose()
