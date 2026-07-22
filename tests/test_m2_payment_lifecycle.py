from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.payment_lifecycle_service import (
    ConsultationPaymentLifecycleError,
    ConsultationPaymentLifecycleService,
)
from app.domain.payments.payment_processing_outcomes import PaymentProcessingOutcome
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.payment_webhook_service import PaymentWebhookService
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


async def _create_test_database(tmp_path, name: str):
    database_path = tmp_path / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, session_factory


async def _seed_reserved_m2_consultation(session, suffix: str = "001"):
    starts_at = datetime.now(timezone.utc) + timedelta(days=2)

    user = User(
        telegram_id=910_000 + int(suffix),
        telegram_username=f"m2_client_{suffix}",
        full_name=f"Клиент М2 {suffix}",
    )
    lawyer = Lawyer(
        full_name=f"Юрист М2 {suffix}",
        email=f"lawyer-{suffix}@example.test",
        is_active=True,
    )
    session.add_all([user, lawyer])
    await session.flush()

    case = Case(
        case_number=f"TEST-M2-LIFECYCLE-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_PAYMENT_PENDING.value,
        title="Оплачиваемая консультация",
        next_action="Перейдите к оплате консультации",
    )
    session.add(case)
    await session.flush()

    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        status=ConsultationStatus.SLOT_RESERVED.value,
        scheduled_at=starts_at,
        client_description="Нужна консультация по делу.",
    )
    session.add(consultation)
    await session.flush()

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

    payment = Payment(
        case_id=case.id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
        title="Оплата консультации",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=PaymentStatus.WAITING_CONFIRMATION,
        provider="fake",
        provider_payment_id=f"provider-{suffix}",
        payment_url=f"https://payments.example.test/{suffix}",
    )
    session.add(payment)
    await session.commit()

    return {
        "user_id": user.id,
        "lawyer_id": lawyer.id,
        "case_id": case.id,
        "consultation_id": consultation.id,
        "slot_id": slot.id,
        "payment_id": payment.id,
    }


async def _audit_count(session, *, case_id: int, action: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_id == case_id,
                    AuditLog.action == action,
                )
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_m2_payment_and_lawyer_confirmation_complete_once(tmp_path):
    engine, session_factory = await _create_test_database(
        tmp_path,
        "m2-payment-lifecycle.db",
    )

    async with session_factory() as session:
        ids = await _seed_reserved_m2_consultation(session)

    async with session_factory() as session:
        case = await session.get(Case, ids["case_id"])
        consultation = await session.get(Consultation, ids["consultation_id"])
        payment = await session.get(Payment, ids["payment_id"])
        slot = await session.get(ConsultationSlot, ids["slot_id"])

        lifecycle = ConsultationPaymentLifecycleService(session)
        prepared = await lifecycle.prepare_payment(
            case=case,
            client_id=ids["user_id"],
            source="test",
        )

        assert prepared.id == consultation.id
        assert prepared.status == ConsultationStatus.PAYMENT_PENDING.value
        assert case.status == CaseStatus.M2_PAYMENT_PENDING.value
        assert slot.status == "held"

        webhook = PaymentWebhookService(session)
        await webhook.process_successful_payment(
            payment=payment,
            case=case,
            provider_payload={"event": "payment.succeeded"},
        )
        await session.commit()

        assert payment.status == PaymentStatus.PAID
        assert payment.processing_outcome == PaymentProcessingOutcome.PROCESSED
        assert payment.manual_review_required is False
        assert payment.processing_error is None
        assert payment.processed_at is not None
        assert consultation.status == ConsultationStatus.PAID_PENDING_CONFIRMATION.value
        assert slot.status == "booked"
        assert slot.hold_expires_at is None
        assert case.status == CaseStatus.M2_PAYMENT_PENDING.value
        assert case.assigned_lawyer_id is None

        webhook_events = await _audit_count(
            session,
            case_id=case.id,
            action="PAYMENT_WEBHOOK_PROCESSED",
        )
        paid_events = await _audit_count(
            session,
            case_id=case.id,
            action="PAYMENT_PAID",
        )

        await webhook.process_successful_payment(
            payment=payment,
            case=case,
            provider_payload={"event": "payment.succeeded.retry"},
        )
        await session.commit()

        assert await _audit_count(
            session,
            case_id=case.id,
            action="PAYMENT_WEBHOOK_PROCESSED",
        ) == webhook_events
        assert await _audit_count(
            session,
            case_id=case.id,
            action="PAYMENT_PAID",
        ) == paid_events

        wrong_lawyer = Lawyer(
            full_name="Чужой юрист",
            email="wrong-lawyer@example.test",
            is_active=True,
        )
        session.add(wrong_lawyer)
        await session.flush()

        with pytest.raises(
            ConsultationPaymentLifecycleError,
            match="только назначенный",
        ):
            await lifecycle.confirm_by_lawyer(
                case=case,
                lawyer_id=wrong_lawyer.id,
                source="test",
            )

        assert consultation.status == ConsultationStatus.PAID_PENDING_CONFIRMATION.value
        assert case.assigned_lawyer_id is None

        booked = await lifecycle.confirm_by_lawyer(
            case=case,
            lawyer_id=ids["lawyer_id"],
            source="test",
        )
        await session.commit()

        assert booked.status == ConsultationStatus.BOOKED.value
        assert booked.lawyer_id == ids["lawyer_id"]
        assert booked.slot_id == ids["slot_id"]
        assert case.assigned_lawyer_id == ids["lawyer_id"]
        assert case.status == CaseStatus.M2_CONSULTATION_BOOKED.value
        assert case.next_action == "Ожидайте консультации в выбранное время"

        confirmed_events = await _audit_count(
            session,
            case_id=case.id,
            action="CONSULTATION_CONFIRMED_BY_LAWYER",
        )
        booked_events = await _audit_count(
            session,
            case_id=case.id,
            action="CONSULTATION_BOOKED_AFTER_LAWYER_CONFIRMATION",
        )
        assert confirmed_events == 1
        assert booked_events == 1

        repeated = await lifecycle.confirm_by_lawyer(
            case=case,
            lawyer_id=ids["lawyer_id"],
            source="test-retry",
        )
        await session.commit()

        assert repeated.status == ConsultationStatus.BOOKED.value
        assert await _audit_count(
            session,
            case_id=case.id,
            action="CONSULTATION_CONFIRMED_BY_LAWYER",
        ) == confirmed_events
        assert await _audit_count(
            session,
            case_id=case.id,
            action="CONSULTATION_BOOKED_AFTER_LAWYER_CONFIRMATION",
        ) == booked_events

    await engine.dispose()


@pytest.mark.asyncio
async def test_prepare_payment_rejects_other_client_without_mutation(tmp_path):
    engine, session_factory = await _create_test_database(
        tmp_path,
        "m2-payment-ownership.db",
    )

    async with session_factory() as session:
        ids = await _seed_reserved_m2_consultation(session, suffix="002")

    async with session_factory() as session:
        case = await session.get(Case, ids["case_id"])
        consultation = await session.get(Consultation, ids["consultation_id"])
        slot = await session.get(ConsultationSlot, ids["slot_id"])

        with pytest.raises(
            ConsultationPaymentLifecycleError,
            match="не принадлежит текущему клиенту",
        ):
            await ConsultationPaymentLifecycleService(session).prepare_payment(
                case=case,
                client_id=ids["user_id"] + 999,
                source="test",
            )

        assert consultation.status == ConsultationStatus.SLOT_RESERVED.value
        assert case.status == CaseStatus.M2_PAYMENT_PENDING.value
        assert slot.status == "held"
        assert slot.consultation_id == consultation.id

    await engine.dispose()


@pytest.mark.asyncio
async def test_webhook_rejects_payment_from_another_case(tmp_path):
    engine, session_factory = await _create_test_database(
        tmp_path,
        "m2-payment-case-ownership.db",
    )

    async with session_factory() as session:
        first = await _seed_reserved_m2_consultation(session, suffix="003")
        second = await _seed_reserved_m2_consultation(session, suffix="004")

    async with session_factory() as session:
        payment = await session.get(Payment, first["payment_id"])
        wrong_case = await session.get(Case, second["case_id"])

        with pytest.raises(ValueError, match="не принадлежит"):
            await PaymentWebhookService(session).process_successful_payment(
                payment=payment,
                case=wrong_case,
            )

        assert payment.status == PaymentStatus.WAITING_CONFIRMATION
        assert payment.processing_outcome is None
        assert payment.processed_at is None

    await engine.dispose()
