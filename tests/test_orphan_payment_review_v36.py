from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.payments.orphan_payment_review_service import (
    OrphanPaymentReviewResolutionError,
    OrphanPaymentReviewService,
)
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
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
from app.models.payment import Payment
from app.models.user import User


async def create_database(tmp_path, name: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, factory


async def create_booked_case(session, *, suffix: int):
    user = User(
        telegram_id=995000 + suffix,
        full_name=f"Клиент orphan review {suffix}",
    )
    lawyer = Lawyer(
        full_name=f"Юрист orphan review {suffix}",
        telegram_id=996000 + suffix,
        is_active=True,
    )
    session.add_all([user, lawyer])
    await session.flush()

    case = Case(
        case_number=f"ORPHAN-REVIEW-{suffix}",
        client_id=user.id,
        route="M2",
        status=CaseStatus.M2_CONSULTATION_BOOKED,
        title="Текущая подтверждённая консультация",
        next_action="Подготовиться к консультации",
    )
    session.add(case)
    await session.flush()

    consultation = Consultation(
        case_id=case.id,
        status=ConsultationStatus.BOOKED,
        lawyer_id=lawyer.id,
    )
    session.add(consultation)
    await session.flush()

    starts_at = datetime.now(timezone.utc) + timedelta(days=2)
    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        status="booked",
        consultation_id=consultation.id,
        held_by_user_id=user.id,
    )
    session.add(slot)
    await session.flush()
    consultation.slot_id = slot.id
    consultation.scheduled_at = slot.starts_at
    return user, lawyer, case, consultation, slot


@pytest.mark.asyncio
async def test_orphan_review_refund_preserves_current_case_and_booking(tmp_path):
    engine, factory = await create_database(tmp_path, "orphan-refund.db")
    async with factory() as session:
        user, lawyer, case, consultation, slot = await create_booked_case(
            session,
            suffix=1,
        )
        missing_consultation_id = consultation.id + 100000
        missing_slot_id = slot.id + 100000
        payment = Payment(
            case_id=case.id,
            payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
            title="Поздний платёж по старой ссылке",
            amount=Decimal("5000.00"),
            currency="RUB",
            status=PaymentStatus.PAID_REVIEW,
            provider="yookassa",
            provider_payment_id="orphan-review-provider-1",
            reservation_key=(
                f"consultation:{missing_consultation_id}:slot:{missing_slot_id}"
            ),
        )
        session.add(payment)
        await session.commit()

        result = await OrphanPaymentReviewService(session).route_to_refund(
            payment_id=payment.id,
            actor_id=None,
            comment="Старая консультация отсутствует, возвращаем только этот платёж",
        )
        await session.commit()
        await session.refresh(case)
        await session.refresh(consultation)
        await session.refresh(slot)

        assert result.status == PaymentStatus.REFUND_PENDING
        assert case.status == CaseStatus.M2_CONSULTATION_BOOKED
        assert case.next_action == "Подготовиться к консультации"
        assert consultation.status == ConsultationStatus.BOOKED
        assert consultation.slot_id == slot.id
        assert slot.status == "booked"
        assert slot.consultation_id == consultation.id

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
        assert audit is not None
        assert audit.new_value["decision"] == "refund_orphan"
        assert audit.new_value["consultation_id"] is None
        assert audit.new_value["orphan_consultation_id"] == missing_consultation_id
        assert audit.new_value["case_context_preserved"] is True

        result = await ConsultationRefundService(session).resolve_refund(
            payment_id=payment.id,
            decision="refunded",
            actor_id=None,
            comment="Фактический возврат подтверждён по операции refund-orphan-1",
        )
        await session.commit()
        await session.refresh(case)
        await session.refresh(consultation)
        await session.refresh(slot)

        assert result.status == PaymentStatus.REFUNDED
        assert case.status == CaseStatus.M2_CONSULTATION_BOOKED
        assert case.next_action == "Подготовиться к консультации"
        assert consultation.status == ConsultationStatus.BOOKED
        assert consultation.slot_id == slot.id
        assert slot.status == "booked"
        assert slot.consultation_id == consultation.id

        refund_audit = (
            await session.execute(
                select(AuditLog)
                .where(
                    AuditLog.entity_id == case.id,
                    AuditLog.action == "CONSULTATION_REFUND_COMPLETED",
                )
                .order_by(AuditLog.id.desc())
            )
        ).scalars().first()
        assert refund_audit is not None
        assert refund_audit.new_value["payment_id"] == payment.id
        assert refund_audit.new_value["status"] == PaymentStatus.REFUNDED

    await engine.dispose()


@pytest.mark.asyncio
async def test_orphan_refund_is_rejected_when_linked_consultation_exists(tmp_path):
    engine, factory = await create_database(tmp_path, "orphan-existing-context.db")
    async with factory() as session:
        user, lawyer, case, consultation, slot = await create_booked_case(
            session,
            suffix=2,
        )
        payment = Payment(
            case_id=case.id,
            payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
            title="Платёж с существующей привязкой",
            amount=Decimal("5000.00"),
            currency="RUB",
            status=PaymentStatus.PAID_REVIEW,
            provider="yookassa",
            provider_payment_id="orphan-review-provider-2",
            reservation_key=PaymentService.consultation_reservation_key(
                consultation.id,
                slot.id,
            ),
        )
        session.add(payment)
        await session.commit()

        with pytest.raises(
            OrphanPaymentReviewResolutionError,
            match="Связанная консультация существует",
        ):
            await OrphanPaymentReviewService(session).route_to_refund(
                payment_id=payment.id,
                actor_id=None,
                comment="Пытаемся обойти обычную сверку",
            )
        await session.rollback()

        payment = await session.get(Payment, payment.id)
        assert payment.status == PaymentStatus.PAID_REVIEW
        consultation = await session.get(Consultation, consultation.id)
        slot = await session.get(ConsultationSlot, slot.id)
        assert consultation.status == ConsultationStatus.BOOKED
        assert slot.status == "booked"

    await engine.dispose()


def test_payment_review_center_exposes_only_refund_for_broken_reservation():
    source = Path("app/api/payment_review_center.py").read_text(encoding="utf-8")
    assert 'context_source == "broken_reservation_key"' in source
    assert 'allowed_actions = ["refund_orphan"]' in source
    assert "Вернуть платёж без изменения дела" in source
    assert "Дело, текущая консультация и слот останутся без изменений" in source
