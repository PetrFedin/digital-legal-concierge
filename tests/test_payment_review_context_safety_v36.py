from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cases.case_history import add_case_history_event
from app.domain.payments.payment_review_service import (
    PaymentReviewResolutionError,
    PaymentReviewService,
)
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
from app.models.payment import Payment
from app.models.user import User


async def create_database(tmp_path, name: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, factory


async def create_context(session, *, suffix: int):
    user = User(
        telegram_id=991000 + suffix,
        full_name=f"Клиент review safety {suffix}",
    )
    lawyer = Lawyer(
        full_name=f"Юрист review safety {suffix}",
        telegram_id=992000 + suffix,
        is_active=True,
    )
    admin = AdminUser(
        full_name=f"Администратор review safety {suffix}",
        username=f"review-safety-{suffix}",
        email=f"review-safety-{suffix}@example.com",
        telegram_id=993000 + suffix,
        password_hash="test-password-hash",
        role="admin",
        is_active=True,
    )
    session.add_all([user, lawyer, admin])
    await session.flush()

    case = Case(
        case_number=f"REVIEW-SAFETY-{suffix}",
        client_id=user.id,
        route="M2",
        status=CaseStatus.M2_SLOT_PENDING,
        title="Безопасная сверка платежа",
        next_action="Выбрать дальнейшее действие",
    )
    session.add(case)
    await session.flush()

    consultation = Consultation(
        case_id=case.id,
        status=ConsultationStatus.SLOT_PENDING,
        lawyer_id=lawyer.id,
    )
    session.add(consultation)
    await session.flush()

    starts_at = datetime.now(timezone.utc) + timedelta(days=2)
    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        status="available",
    )
    session.add(slot)
    await session.flush()

    payment = Payment(
        case_id=case.id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
        title="Оплата консультации",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=PaymentStatus.PAID_REVIEW,
        provider="yookassa",
        provider_payment_id=f"review-safety-provider-{suffix}",
        reservation_key=PaymentService.consultation_reservation_key(
            consultation.id,
            slot.id,
        ),
    )
    session.add(payment)
    await session.flush()
    return {
        "user": user,
        "lawyer": lawyer,
        "admin": admin,
        "case": case,
        "consultation": consultation,
        "slot": slot,
        "payment": payment,
    }


@pytest.mark.asyncio
async def test_refund_of_late_extra_payment_preserves_booked_consultation(tmp_path):
    engine, factory = await create_database(tmp_path, "review-preserve-booking.db")
    async with factory() as session:
        context = await create_context(session, suffix=1)
        case = context["case"]
        consultation = context["consultation"]
        slot = context["slot"]
        payment = context["payment"]

        slot.status = "booked"
        slot.consultation_id = consultation.id
        slot.held_by_user_id = context["user"].id
        consultation.status = ConsultationStatus.BOOKED
        consultation.slot_id = slot.id
        consultation.scheduled_at = slot.starts_at
        case.status = CaseStatus.M2_CONSULTATION_BOOKED
        case.next_action = "Подготовиться к назначенной консультации"
        await session.commit()

        payment, resolved_consultation = await PaymentReviewService(session).resolve(
            payment_id=payment.id,
            decision="refund_pending",
            actor_id=context["admin"].id,
            comment="Поздний лишний платёж возвращаем без отмены записи",
        )
        await session.commit()
        await session.refresh(slot)
        await session.refresh(case)

        assert payment.status == PaymentStatus.REFUND_PENDING
        assert resolved_consultation.id == consultation.id
        assert resolved_consultation.status == ConsultationStatus.BOOKED
        assert resolved_consultation.slot_id == slot.id
        assert slot.status == "booked"
        assert slot.consultation_id == consultation.id
        assert case.status == CaseStatus.M2_CONSULTATION_BOOKED
        assert case.next_action == "Подготовиться к назначенной консультации"

        audit = (
            await session.execute(
                select(AuditLog)
                .where(
                    AuditLog.entity_id == case.id,
                    AuditLog.action == "CONSULTATION_PAYMENT_REVIEW_RESOLVED",
                )
                .order_by(AuditLog.id.desc())
            )
        ).scalars().first()
        assert audit.new_value["decision"] == "refund_pending"
        assert audit.new_value["booking_preserved"] is True
        assert audit.new_value["case_context_preserved"] is True

    await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_ambiguous_payment_requires_explicit_consultation_choice(tmp_path):
    engine, factory = await create_database(tmp_path, "review-legacy-context.db")
    async with factory() as session:
        context = await create_context(session, suffix=2)
        payment = context["payment"]
        payment.reservation_key = None
        second = Consultation(
            case_id=context["case"].id,
            status=ConsultationStatus.SLOT_PENDING,
            lawyer_id=context["lawyer"].id,
        )
        session.add(second)
        await session.commit()

        with pytest.raises(PaymentReviewResolutionError, match="Выберите консультацию"):
            await PaymentReviewService(session).resolve(
                payment_id=payment.id,
                decision="refund_pending",
                actor_id=context["admin"].id,
                comment="Нужна явная привязка старого платежа",
            )
        await session.rollback()

        payment = await session.get(Payment, payment.id)
        assert payment.status == PaymentStatus.PAID_REVIEW

        payment, selected = await PaymentReviewService(session).resolve(
            payment_id=payment.id,
            decision="refund_pending",
            consultation_id=context["consultation"].id,
            actor_id=context["admin"].id,
            comment="После сверки платёж относится к первой консультации",
        )
        await session.commit()

        assert selected.id == context["consultation"].id
        assert payment.status == PaymentStatus.REFUND_PENDING

    await engine.dispose()


@pytest.mark.asyncio
async def test_old_payment_review_does_not_mutate_newer_booked_consultation(tmp_path):
    engine, factory = await create_database(tmp_path, "review-old-vs-new.db")
    async with factory() as session:
        context = await create_context(session, suffix=3)
        old_consultation = context["consultation"]
        payment = context["payment"]
        case = context["case"]

        newer = Consultation(
            case_id=case.id,
            status=ConsultationStatus.BOOKED,
            lawyer_id=context["lawyer"].id,
        )
        session.add(newer)
        await session.flush()
        starts_at = datetime.now(timezone.utc) + timedelta(days=3)
        new_slot = ConsultationSlot(
            lawyer_id=context["lawyer"].id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            status="booked",
            consultation_id=newer.id,
            held_by_user_id=context["user"].id,
        )
        session.add(new_slot)
        await session.flush()
        newer.slot_id = new_slot.id
        newer.scheduled_at = new_slot.starts_at
        case.status = CaseStatus.M2_CONSULTATION_BOOKED
        case.next_action = "Подготовиться к новой консультации"
        payment.reservation_key = f"consultation:{old_consultation.id}:slot:999999"
        await session.commit()

        payment, selected = await PaymentReviewService(session).resolve(
            payment_id=payment.id,
            decision="refund_pending",
            actor_id=context["admin"].id,
            comment="Возвращаем платёж старой записи, новую встречу не меняем",
        )
        await session.commit()
        await session.refresh(newer)
        await session.refresh(new_slot)
        await session.refresh(case)

        assert selected.id == old_consultation.id
        assert selected.status == ConsultationStatus.CANCELLED
        assert payment.status == PaymentStatus.REFUND_PENDING
        assert newer.status == ConsultationStatus.BOOKED
        assert newer.slot_id == new_slot.id
        assert new_slot.status == "booked"
        assert new_slot.consultation_id == newer.id
        assert case.status == CaseStatus.M2_CONSULTATION_BOOKED
        assert case.next_action == "Подготовиться к новой консультации"

    await engine.dispose()


@pytest.mark.asyncio
async def test_inactive_link_review_cannot_be_silently_accepted_into_booking(tmp_path):
    engine, factory = await create_database(tmp_path, "review-expired-origin.db")
    async with factory() as session:
        context = await create_context(session, suffix=4)
        case = context["case"]
        consultation = context["consultation"]
        slot = context["slot"]
        payment = context["payment"]

        slot.status = "booked"
        slot.consultation_id = consultation.id
        slot.held_by_user_id = context["user"].id
        consultation.status = ConsultationStatus.BOOKED
        consultation.slot_id = slot.id
        consultation.scheduled_at = slot.starts_at
        case.status = CaseStatus.M2_CONSULTATION_BOOKED
        await add_case_history_event(
            session,
            actor_type="payment_provider",
            actor_id=None,
            case_id=case.id,
            action="CONSULTATION_PAYMENT_REVIEW_REQUIRED",
            old_value={"status": PaymentStatus.EXPIRED},
            new_value={
                "payment_id": payment.id,
                "status": PaymentStatus.PAID_REVIEW,
                "reason": "Поздняя оплата истёкшей ссылки",
            },
        )
        await session.commit()

        with pytest.raises(PaymentReviewResolutionError, match="контролируемый возврат"):
            await PaymentReviewService(session).resolve(
                payment_id=payment.id,
                decision="confirm_existing",
                actor_id=context["admin"].id,
                comment="Проверяем запрет принятия позднего платежа",
            )
        await session.rollback()

        payment = await session.get(Payment, payment.id)
        consultation = await session.get(Consultation, consultation.id)
        slot = await session.get(ConsultationSlot, slot.id)
        assert payment.status == PaymentStatus.PAID_REVIEW
        assert consultation.status == ConsultationStatus.BOOKED
        assert slot.status == "booked"
        assert slot.consultation_id == consultation.id

    await engine.dispose()
