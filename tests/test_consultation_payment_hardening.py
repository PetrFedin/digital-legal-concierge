from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
from app.models.payment import Payment
from app.models.user import User


async def create_database(tmp_path, name: str):
    database_path = tmp_path / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, session_factory


async def create_paid_consultation_context(
    session,
    *,
    suffix: int,
    expired: bool = False,
    reservation_key: str | None = None,
):
    lawyer = Lawyer(
        full_name=f"Юрист {suffix}",
        is_active=True,
        workload_limit=10,
    )
    user = User(
        telegram_id=700000 + suffix,
        full_name=f"Клиент {suffix}",
    )
    session.add_all([lawyer, user])
    await session.flush()

    case = Case(
        case_number=f"PAY-HARD-{suffix}",
        client_id=user.id,
        route="M2",
        status=CaseStatus.M2_PAYMENT_PENDING,
        title="Тест защищённой оплаты консультации",
    )
    session.add(case)
    await session.flush()

    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        status=ConsultationStatus.PAYMENT_PENDING,
        client_description=(
            "Нужно проверить договор и порядок дальнейших действий по спору."
        ),
        subject_type="new_or_other",
    )
    session.add(consultation)
    await session.flush()

    starts_at = datetime.now(timezone.utc) + timedelta(days=1)
    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        status="held",
        held_by_user_id=user.id,
        consultation_id=consultation.id,
        hold_expires_at=(
            datetime.now(timezone.utc) - timedelta(minutes=1)
            if expired
            else datetime.now(timezone.utc) + timedelta(minutes=10)
        ),
    )
    session.add(slot)
    await session.flush()

    consultation.slot_id = slot.id
    consultation.scheduled_at = slot.starts_at
    expected_key = PaymentService.consultation_reservation_key(
        consultation.id,
        slot.id,
    )
    payment = Payment(
        case_id=case.id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
        title="Оплата консультации",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=PaymentStatus.WAITING_CONFIRMATION,
        provider="fake",
        provider_payment_id=f"fake-{suffix}",
        payment_url=f"http://test/pay/{suffix}",
        reservation_key=(
            expected_key if reservation_key is None else reservation_key
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
        "expected_key": expected_key,
    }


@pytest.mark.asyncio
async def test_successful_consultation_payment_is_idempotent(tmp_path):
    engine, session_factory = await create_database(tmp_path, "payment-idempotent.db")

    async with session_factory() as session:
        context = await create_paid_consultation_context(session, suffix=1)
        await session.commit()

        service = PaymentWebhookService(session)
        await service.process_successful_payment(
            payment=context["payment"],
            case=context["case"],
            provider_payload={"event": "first"},
        )
        await session.commit()

        await service.process_successful_payment(
            payment=context["payment"],
            case=context["case"],
            provider_payload={"event": "duplicate"},
        )
        await session.commit()

        payment = await session.get(Payment, context["payment"].id)
        consultation = await session.get(
            Consultation,
            context["consultation"].id,
        )
        slot = await session.get(ConsultationSlot, context["slot"].id)
        case = await session.get(Case, context["case"].id)

        assert payment.status == PaymentStatus.PAID
        assert consultation.status == ConsultationStatus.BOOKED
        assert slot.status == "booked"
        assert case.status == CaseStatus.M2_CONSULTATION_BOOKED

        processed_count = (
            await session.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_id == case.id,
                    AuditLog.action == "PAYMENT_WEBHOOK_PROCESSED",
                )
            )
        ).scalar_one()
        paid_count = (
            await session.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_id == case.id,
                    AuditLog.action == "PAYMENT_PAID",
                )
            )
        ).scalar_one()
        assert processed_count == 1
        assert paid_count == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_payment_after_expired_hold_goes_to_manual_review(tmp_path):
    engine, session_factory = await create_database(tmp_path, "payment-expired.db")

    async with session_factory() as session:
        context = await create_paid_consultation_context(
            session,
            suffix=2,
            expired=True,
        )
        await session.commit()

        await PaymentWebhookService(session).process_successful_payment(
            payment=context["payment"],
            case=context["case"],
            provider_payload={"event": "paid_after_expiry"},
        )
        await session.commit()

        payment = await session.get(Payment, context["payment"].id)
        consultation = await session.get(
            Consultation,
            context["consultation"].id,
        )
        slot = await session.get(ConsultationSlot, context["slot"].id)
        case = await session.get(Case, context["case"].id)

        assert payment.status == PaymentStatus.PAID_REVIEW
        assert consultation.status == ConsultationStatus.SLOT_PENDING
        assert consultation.slot_id is None
        assert slot.status == "available"
        assert slot.consultation_id is None
        assert case.status == CaseStatus.M2_SLOT_PENDING

    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_payment_link_cannot_book_new_slot(tmp_path):
    engine, session_factory = await create_database(tmp_path, "payment-stale.db")

    async with session_factory() as session:
        context = await create_paid_consultation_context(
            session,
            suffix=3,
            reservation_key="consultation:old:slot:old",
        )
        await session.commit()

        await PaymentWebhookService(session).process_successful_payment(
            payment=context["payment"],
            case=context["case"],
            provider_payload={"event": "stale_link_paid"},
        )
        await session.commit()

        payment = await session.get(Payment, context["payment"].id)
        consultation = await session.get(
            Consultation,
            context["consultation"].id,
        )
        slot = await session.get(ConsultationSlot, context["slot"].id)

        assert payment.status == PaymentStatus.PAID_REVIEW
        assert consultation.status == ConsultationStatus.PAYMENT_PENDING
        assert slot.status == "held"

    await engine.dispose()


@pytest.mark.asyncio
async def test_late_failed_event_does_not_downgrade_paid_payment(tmp_path):
    engine, session_factory = await create_database(tmp_path, "payment-late-fail.db")

    async with session_factory() as session:
        context = await create_paid_consultation_context(session, suffix=4)
        await session.commit()

        service = PaymentWebhookService(session)
        await service.process_successful_payment(
            payment=context["payment"],
            case=context["case"],
            provider_payload={"event": "paid"},
        )
        await session.commit()
        await service.process_failed_payment(
            payment=context["payment"],
            case=context["case"],
            provider_payload={"event": "late_failed"},
        )
        await session.commit()

        payment = await session.get(Payment, context["payment"].id)
        assert payment.status == PaymentStatus.PAID

    await engine.dispose()


@pytest.mark.asyncio
async def test_new_reservation_expires_old_pending_payment_link(tmp_path):
    engine, session_factory = await create_database(tmp_path, "payment-link.db")

    async with session_factory() as session:
        context = await create_paid_consultation_context(session, suffix=5)
        current_payment = context["payment"]
        current_payment.status = PaymentStatus.EXPIRED
        legacy_payment = Payment(
            case_id=context["case"].id,
            payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
            title="Старая ссылка",
            amount=Decimal("5000.00"),
            currency="RUB",
            status=PaymentStatus.WAITING_CONFIRMATION,
            reservation_key=None,
        )
        session.add(legacy_payment)
        await session.commit()

        new_payment = await PaymentService(session).get_or_create_payment(
            case=context["case"],
            payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
            amount=Decimal("5000.00"),
        )
        await session.commit()

        await session.refresh(legacy_payment)
        assert legacy_payment.status == PaymentStatus.EXPIRED
        assert new_payment.id != legacy_payment.id
        assert new_payment.reservation_key == context["expected_key"]
        assert new_payment.status == PaymentStatus.PENDING

    await engine.dispose()
