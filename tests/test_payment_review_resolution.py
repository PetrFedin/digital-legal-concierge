from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.slot_service import SlotUnavailableError
from app.domain.payments.payment_review_service import PaymentReviewService
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.admin_user import AdminUser
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.user import User


async def create_database(tmp_path, name: str):
    path = tmp_path / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, factory


async def create_review_context(
    session,
    *,
    suffix: int,
    consultation_status: str = ConsultationStatus.SLOT_PENDING,
):
    user = User(
        telegram_id=960000 + suffix,
        full_name=f"Клиент проверки {suffix}",
    )
    lawyer = Lawyer(
        full_name=f"Юрист проверки {suffix}",
        telegram_id=970000 + suffix,
        is_active=True,
    )
    admin = AdminUser(
        full_name=f"Администратор проверки {suffix}",
        username=f"review-admin-{suffix}",
        email=f"review-admin-{suffix}@example.com",
        telegram_id=980000 + suffix,
        password_hash="test-password-hash",
        role="admin",
        is_active=True,
    )
    session.add_all([user, lawyer, admin])
    await session.flush()

    case = Case(
        case_number=f"PAYMENT-REVIEW-{suffix}",
        client_id=user.id,
        route="M2",
        status=CaseStatus.M2_SLOT_PENDING,
        title="Проверка полученного платежа",
    )
    session.add(case)
    await session.flush()

    consultation = Consultation(
        case_id=case.id,
        status=consultation_status,
        lawyer_id=lawyer.id,
    )
    session.add(consultation)
    await session.flush()

    payment = Payment(
        case_id=case.id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
        title="Оплата консультации",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=PaymentStatus.PAID_REVIEW,
        provider="yookassa",
        provider_payment_id=f"review-provider-{suffix}",
        reservation_key=f"consultation:{consultation.id}:slot:expired",
    )
    starts_at = datetime.now(timezone.utc) + timedelta(days=1)
    available_slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        status="available",
    )
    session.add_all([payment, available_slot])
    await session.flush()
    return {
        "user": user,
        "lawyer": lawyer,
        "admin": admin,
        "case": case,
        "consultation": consultation,
        "payment": payment,
        "available_slot": available_slot,
    }


@pytest.mark.asyncio
async def test_assign_new_slot_resolves_review_without_second_payment(tmp_path):
    engine, factory = await create_database(tmp_path, "review-assign.db")
    async with factory() as session:
        context = await create_review_context(session, suffix=1)
        await session.commit()

        payment, consultation = await PaymentReviewService(session).resolve(
            payment_id=context["payment"].id,
            decision="assign_slot",
            slot_id=context["available_slot"].id,
            actor_id=context["admin"].id,
            comment="Платёж подтверждён провайдером, назначен новый слот",
        )
        await session.commit()
        await session.refresh(payment)
        await session.refresh(consultation)
        slot = await session.get(
            ConsultationSlot,
            context["available_slot"].id,
        )
        case = await session.get(Case, context["case"].id)

        assert payment.status == PaymentStatus.PAID
        assert payment.reservation_key == (
            PaymentService.consultation_reservation_key(
                consultation.id,
                slot.id,
            )
        )
        assert consultation.status == ConsultationStatus.BOOKED
        assert consultation.slot_id == slot.id
        assert consultation.scheduled_at == slot.starts_at
        assert slot.status == "booked"
        assert slot.consultation_id == consultation.id
        assert case.status == CaseStatus.M2_CONSULTATION_BOOKED

        audit = (
            await session.execute(
                select(AuditLog)
                .where(
                    AuditLog.entity_id == case.id,
                    AuditLog.action
                    == "CONSULTATION_PAYMENT_REVIEW_RESOLVED",
                )
                .order_by(AuditLog.id.desc())
            )
        ).scalars().first()
        assert audit.actor_id == context["admin"].id
        assert audit.new_value["decision"] == "assign_slot"

        notification_count = (
            await session.execute(
                select(func.count(Notification.id)).where(
                    Notification.event_code
                    == "CONSULTATION_PAYMENT_REVIEW_RESOLVED"
                )
            )
        ).scalar_one()
        assert notification_count == 3

    await engine.dispose()


@pytest.mark.asyncio
async def test_occupied_slot_rolls_back_review_resolution(tmp_path):
    engine, factory = await create_database(tmp_path, "review-rollback.db")
    async with factory() as session:
        context = await create_review_context(session, suffix=2)
        context["available_slot"].status = "booked"
        await session.commit()
        payment_id = context["payment"].id
        consultation_id = context["consultation"].id
        slot_id = context["available_slot"].id

        with pytest.raises(SlotUnavailableError):
            await PaymentReviewService(session).resolve(
                payment_id=payment_id,
                decision="assign_slot",
                slot_id=slot_id,
                actor_id=context["admin"].id,
                comment="Попытка назначить занятый слот",
            )
        await session.rollback()

        payment = await session.get(Payment, payment_id)
        consultation = await session.get(Consultation, consultation_id)
        slot = await session.get(ConsultationSlot, slot_id)
        assert payment.status == PaymentStatus.PAID_REVIEW
        assert consultation.status == ConsultationStatus.SLOT_PENDING
        assert consultation.slot_id is None
        assert slot.status == "booked"

    await engine.dispose()


@pytest.mark.asyncio
async def test_confirm_existing_valid_booking_resolves_false_review(tmp_path):
    engine, factory = await create_database(tmp_path, "review-existing.db")
    async with factory() as session:
        context = await create_review_context(
            session,
            suffix=3,
            consultation_status=ConsultationStatus.BOOKED,
        )
        slot = context["available_slot"]
        slot.status = "booked"
        slot.consultation_id = context["consultation"].id
        slot.held_by_user_id = context["user"].id
        context["consultation"].slot_id = slot.id
        context["consultation"].scheduled_at = slot.starts_at
        await session.commit()

        payment, consultation = await PaymentReviewService(session).resolve(
            payment_id=context["payment"].id,
            decision="confirm_existing",
            actor_id=context["admin"].id,
            comment="Связь со слотом проверена вручную",
        )
        await session.commit()

        assert payment.status == PaymentStatus.PAID
        assert consultation.status == ConsultationStatus.BOOKED
        assert payment.reservation_key == (
            PaymentService.consultation_reservation_key(
                consultation.id,
                slot.id,
            )
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_review_can_be_routed_to_controlled_refund(tmp_path):
    engine, factory = await create_database(tmp_path, "review-refund.db")
    async with factory() as session:
        context = await create_review_context(session, suffix=4)
        held_slot = context["available_slot"]
        held_slot.status = "held"
        held_slot.consultation_id = context["consultation"].id
        held_slot.held_by_user_id = context["user"].id
        held_slot.hold_expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=10
        )
        context["consultation"].slot_id = held_slot.id
        context["consultation"].scheduled_at = held_slot.starts_at
        context["consultation"].status = ConsultationStatus.PAYMENT_PENDING
        await session.commit()

        payment, consultation = await PaymentReviewService(session).resolve(
            payment_id=context["payment"].id,
            decision="refund_pending",
            actor_id=context["admin"].id,
            comment="Клиент выбрал возврат вместо нового времени",
        )
        await session.commit()
        await session.refresh(held_slot)
        case = await session.get(Case, context["case"].id)

        assert payment.status == PaymentStatus.REFUND_PENDING
        assert consultation.status == ConsultationStatus.CANCELLED
        assert consultation.slot_id is None
        assert consultation.scheduled_at is None
        assert held_slot.status == "available"
        assert held_slot.consultation_id is None
        assert case.next_action == "Обработать возврат полученного платежа"

        refund_notifications = (
            await session.execute(
                select(func.count(Notification.id)).where(
                    Notification.event_code
                    == "CONSULTATION_PAYMENT_REVIEW_REFUND_PENDING"
                )
            )
        ).scalar_one()
        assert refund_notifications == 2

    await engine.dispose()
