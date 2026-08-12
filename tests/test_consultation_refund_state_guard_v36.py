from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.refund_service import (
    ConsultationRefundService,
    ConsultationRefundStateConflict,
)
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.payment import Payment
from app.models.user import User


@asynccontextmanager
async def database(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'm2-refund-state-v36.db'}"
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield factory
    finally:
        await engine.dispose()


async def seed_booked_with_payment(session, *, payment_status: PaymentStatus):
    user = User(telegram_id=984100 + len(session.new), full_name="Клиент Refund Guard")
    lawyer = Lawyer(full_name="Юрист Refund Guard", is_active=True)
    session.add_all([user, lawyer])
    await session.flush()

    case = Case(
        case_number=f"M2-REFUND-GUARD-{payment_status}",
        client_id=user.id,
        route=RouteCode.M2,
        status=CaseStatus.M2_CONSULTATION_BOOKED,
        title="Проверка возврата",
    )
    session.add(case)
    await session.flush()

    starts_at = datetime.now(timezone.utc) + timedelta(days=4)
    consultation = Consultation(
        case_id=case.id,
        status=ConsultationStatus.BOOKED,
        client_description="Нужна юридическая консультация по договору.",
        subject_type="new_or_other",
        lawyer_id=lawyer.id,
        scheduled_at=starts_at,
    )
    session.add(consultation)
    await session.flush()

    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        status="booked",
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
        status=payment_status,
        reservation_key=PaymentService.consultation_reservation_key(
            consultation.id,
            slot.id,
        ),
    )
    session.add(payment)
    await session.commit()
    return user, case, consultation, slot, payment


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payment_status",
    [
        PaymentStatus.REFUND_PENDING,
        PaymentStatus.REFUND_DECLINED,
        PaymentStatus.REFUNDED,
    ],
)
async def test_active_consultation_with_existing_refund_state_fails_closed(
    tmp_path,
    payment_status,
):
    async with database(tmp_path) as factory:
        async with factory() as session:
            user, case, consultation, slot, payment = await seed_booked_with_payment(
                session,
                payment_status=payment_status,
            )

            with pytest.raises(ConsultationRefundStateConflict):
                await ConsultationRefundService(session).request_cancellation(
                    consultation=consultation,
                    case=case,
                    client_id=user.id,
                    reason="Клиент просит отменить повторно",
                )
            await session.rollback()
            await session.refresh(case)
            await session.refresh(consultation)
            await session.refresh(slot)
            await session.refresh(payment)

            assert case.status == CaseStatus.M2_CONSULTATION_BOOKED
            assert consultation.status == ConsultationStatus.BOOKED
            assert consultation.slot_id == slot.id
            assert slot.status == "booked"
            assert slot.consultation_id == consultation.id
            assert payment.status == payment_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payment_status",
    [
        PaymentStatus.REFUND_PENDING,
        PaymentStatus.REFUND_DECLINED,
        PaymentStatus.REFUNDED,
    ],
)
async def test_cancelled_consultation_with_existing_refund_state_is_idempotent(
    tmp_path,
    payment_status,
):
    async with database(tmp_path) as factory:
        async with factory() as session:
            user, case, consultation, slot, payment = await seed_booked_with_payment(
                session,
                payment_status=payment_status,
            )
            consultation.status = ConsultationStatus.CANCELLED
            consultation.slot_id = None
            consultation.scheduled_at = None
            slot.status = "free"
            slot.consultation_id = None
            await session.commit()

            same_consultation, same_payment = await ConsultationRefundService(
                session
            ).request_cancellation(
                consultation=consultation,
                case=case,
                client_id=user.id,
                reason="Повторный callback отмены",
            )
            await session.commit()

            assert same_consultation.id == consultation.id
            assert same_consultation.status == ConsultationStatus.CANCELLED
            assert same_payment.id == payment.id
            assert same_payment.status == payment_status


def test_telegram_refund_copy_uses_money_truth_and_has_financial_recovery():
    source = Path("app/bot/screens/consultations.py").read_text(encoding="utf-8")

    cancel_start = source.index('@router.callback_query(lambda c: c.data == "consult_cancel")')
    confirm_start = source.index(
        '@router.callback_query(lambda c: c.data == "consult_cancel_confirm")',
        cancel_start,
    )
    confirmation_screen = source[cancel_start:confirm_start]

    assert "Если по этой записи деньги уже были получены" in confirmation_screen
    assert "Если фактической оплаты не было" in confirmation_screen
    assert "payments_disabled()" not in confirmation_screen
    assert "Онлайн-оплата для этой записи не использовалась" not in source

    confirm_handler = source[confirm_start:]
    assert "except ConsultationRefundStateConflict" in confirm_handler
    assert "Возврат требует сверки" in confirm_handler
    assert "деньги не были возвращены дважды" in confirm_handler
    assert "Текущая консультация, слот и статус дела не изменены" in confirm_handler
    assert '("💳 Оплаты", "payments_open")' in confirm_handler
    assert '("✉️ Написать команде", "message_create")' in confirm_handler
    assert '("👨‍⚖ Открыть текущую запись", "consultation_booked_open")' in confirm_handler
    assert '("📁 Моё дело", "my_case_open")' in confirm_handler
    assert '("🏠 Главная", "nav_home")' in confirm_handler
    assert "По этой записи полученных денег не было, возврат не требуется" in confirm_handler
