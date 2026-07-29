from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.consultation_service import ConsultationService
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.domain.payments.refund_service import ConsultationRefundService
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
    path = tmp_path / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, factory


async def create_context(session, *, suffix: int, started: bool = False):
    lawyer = Lawyer(
        full_name=f"Юрист возврата {suffix}",
        is_active=True,
        workload_limit=10,
    )
    user = User(
        telegram_id=910000 + suffix,
        full_name=f"Клиент возврата {suffix}",
    )
    session.add_all([lawyer, user])
    await session.flush()

    case = Case(
        case_number=f"REFUND-{suffix}",
        client_id=user.id,
        route="M2",
        status=CaseStatus.M2_CONSULTATION_BOOKED,
        title="Возврат консультации",
    )
    session.add(case)
    await session.flush()

    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        status=ConsultationStatus.BOOKED,
    )
    session.add(consultation)
    await session.flush()

    starts_at = (
        datetime.now(timezone.utc) - timedelta(minutes=30)
        if started
        else datetime.now(timezone.utc) + timedelta(days=1)
    )
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
    consultation.scheduled_at = slot.starts_at
    payment = Payment(
        case_id=case.id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
        title="Оплата консультации",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=PaymentStatus.PAID,
        provider="fake",
        provider_payment_id=f"refund-{suffix}",
        reservation_key=PaymentService.consultation_reservation_key(
            consultation.id,
            slot.id,
        ),
    )
    session.add(payment)
    await session.flush()
    return {
        "lawyer": lawyer,
        "user": user,
        "case": case,
        "consultation": consultation,
        "slot": slot,
        "payment": payment,
    }


@pytest.mark.asyncio
async def test_paid_cancellation_creates_refund_request_and_releases_slot(tmp_path):
    engine, factory = await create_database(tmp_path, "refund-request.db")
    async with factory() as session:
        context = await create_context(session, suffix=1)
        await session.commit()

        await ConsultationService(session).cancel(
            consultation=context["consultation"],
            case=context["case"],
            actor_type="client",
            actor_id=context["user"].id,
            comment="Клиент отменил запись",
        )
        await session.commit()

        consultation = await session.get(
            Consultation,
            context["consultation"].id,
        )
        slot = await session.get(ConsultationSlot, context["slot"].id)
        payment = await session.get(Payment, context["payment"].id)
        assert consultation.status == ConsultationStatus.CANCELLED
        assert consultation.slot_id is None
        assert consultation.scheduled_at is None
        assert slot.status == "available"
        assert slot.consultation_id is None
        assert slot.held_by_user_id is None
        assert payment.status == PaymentStatus.REFUND_PENDING

        audit_count = (
            await session.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_id == context["case"].id,
                    AuditLog.action == "CONSULTATION_CANCELLATION_REQUESTED",
                )
            )
        ).scalar_one()
        notification_count = (
            await session.execute(
                select(func.count(Notification.id)).where(
                    Notification.case_id == context["case"].id,
                    Notification.event_code
                    == "CONSULTATION_CANCELLATION_REQUESTED",
                )
            )
        ).scalar_one()
        assert audit_count == 1
        assert notification_count == 3
    await engine.dispose()


@pytest.mark.asyncio
async def test_started_consultation_cannot_create_refund_request(tmp_path):
    engine, factory = await create_database(tmp_path, "refund-started.db")
    async with factory() as session:
        context = await create_context(session, suffix=2, started=True)
        await session.commit()
        consultation_id = context["consultation"].id
        slot_id = context["slot"].id
        payment_id = context["payment"].id

        with pytest.raises(ValueError, match="после её начала"):
            await ConsultationService(session).cancel(
                consultation=context["consultation"],
                case=context["case"],
                actor_type="client",
                actor_id=context["user"].id,
                comment="Поздняя отмена",
            )
        await session.rollback()

        consultation = await session.get(Consultation, consultation_id)
        slot = await session.get(ConsultationSlot, slot_id)
        payment = await session.get(Payment, payment_id)
        assert consultation.status == ConsultationStatus.BOOKED
        assert consultation.slot_id == slot.id
        assert slot.status == "booked"
        assert payment.status == PaymentStatus.PAID
    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_can_mark_refund_completed(tmp_path):
    engine, factory = await create_database(tmp_path, "refund-completed.db")
    async with factory() as session:
        context = await create_context(session, suffix=3)
        await session.commit()
        _consultation, payment = await ConsultationRefundService(
            session
        ).request_cancellation(
            consultation=context["consultation"],
            case=context["case"],
            client_id=context["user"].id,
            reason="Клиент отменил",
        )
        await session.commit()

        await ConsultationRefundService(session).resolve_refund(
            payment_id=payment.id,
            decision="refunded",
            actor_id=77,
            comment="Возврат выполнен в кабинете провайдера, операция R-100",
        )
        await session.commit()

        await session.refresh(payment)
        assert payment.status == PaymentStatus.REFUNDED
        event_count = (
            await session.execute(
                select(func.count(Notification.id)).where(
                    Notification.case_id == context["case"].id,
                    Notification.event_code == "CONSULTATION_REFUNDED",
                )
            )
        ).scalar_one()
        assert event_count == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_decline_has_explicit_stable_status(tmp_path):
    engine, factory = await create_database(tmp_path, "refund-declined.db")
    async with factory() as session:
        context = await create_context(session, suffix=4)
        await session.commit()
        _consultation, payment = await ConsultationRefundService(
            session
        ).request_cancellation(
            consultation=context["consultation"],
            case=context["case"],
            client_id=context["user"].id,
            reason="Клиент отменил",
        )
        await session.commit()

        await ConsultationRefundService(session).resolve_refund(
            payment_id=payment.id,
            decision="declined",
            actor_id=78,
            comment="Отказ после ручной проверки условий отмены",
        )
        await session.commit()

        await session.refresh(payment)
        assert payment.status == PaymentStatus.REFUND_DECLINED
    await engine.dispose()


@pytest.mark.asyncio
async def test_late_webhooks_do_not_change_refund_pending(tmp_path):
    engine, factory = await create_database(tmp_path, "refund-webhook.db")
    async with factory() as session:
        context = await create_context(session, suffix=5)
        await session.commit()
        _consultation, payment = await ConsultationRefundService(
            session
        ).request_cancellation(
            consultation=context["consultation"],
            case=context["case"],
            client_id=context["user"].id,
            reason="Клиент отменил",
        )
        await session.commit()

        service = PaymentWebhookService(session)
        await service.process_successful_payment(
            payment=payment,
            case=context["case"],
            provider_payload={"event": "late_paid"},
        )
        await service.process_failed_payment(
            payment=payment,
            case=context["case"],
            provider_payload={"event": "late_failed"},
        )
        await session.commit()

        await session.refresh(payment)
        assert payment.status == PaymentStatus.REFUND_PENDING
    await engine.dispose()


@pytest.mark.asyncio
async def test_late_webhooks_do_not_change_declined_refund(tmp_path):
    engine, factory = await create_database(tmp_path, "refund-declined-webhook.db")
    async with factory() as session:
        context = await create_context(session, suffix=6)
        await session.commit()
        _consultation, payment = await ConsultationRefundService(
            session
        ).request_cancellation(
            consultation=context["consultation"],
            case=context["case"],
            client_id=context["user"].id,
            reason="Клиент отменил",
        )
        await session.commit()
        await ConsultationRefundService(session).resolve_refund(
            payment_id=payment.id,
            decision="declined",
            actor_id=79,
            comment="Отказ после проверки",
        )
        await session.commit()

        service = PaymentWebhookService(session)
        await service.process_successful_payment(
            payment=payment,
            case=context["case"],
            provider_payload={"event": "late_paid"},
        )
        await service.process_failed_payment(
            payment=payment,
            case=context["case"],
            provider_payload={"event": "late_failed"},
        )
        await session.commit()

        await session.refresh(payment)
        assert payment.status == PaymentStatus.REFUND_DECLINED
    await engine.dispose()
