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
from app.domain.consultations.slot_service import SlotUnavailableError
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
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
        f"sqlite+aiosqlite:///{tmp_path / 'm2-expired-reservation-v36.db'}"
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield factory
    finally:
        await engine.dispose()


async def seed_expired_hold(session, *, telegram_id: int, case_number: str):
    user = User(telegram_id=telegram_id, full_name="Клиент Истёкший Резерв")
    lawyer = Lawyer(full_name=f"Юрист {case_number}", is_active=True)
    session.add_all([user, lawyer])
    await session.flush()
    case = Case(
        case_number=case_number,
        client_id=user.id,
        route=RouteCode.M2,
        status=CaseStatus.M2_PAYMENT_PENDING,
        title="Истёкший резерв консультации",
    )
    session.add(case)
    await session.flush()
    consultation = Consultation(
        case_id=case.id,
        status=ConsultationStatus.PAYMENT_PENDING,
        client_description=(
            "Нужна консультация по спору и понятный следующий юридический шаг."
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
        hold_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        held_by_user_id=user.id,
        consultation_id=consultation.id,
    )
    session.add(slot)
    await session.flush()
    consultation.slot_id = slot.id
    await session.commit()
    return user, case, consultation, slot


async def assert_slot_selection_restored(session, *, case, consultation, slot):
    await session.refresh(consultation)
    await session.refresh(slot)
    await session.refresh(case)

    assert consultation.status == ConsultationStatus.SLOT_SELECTION
    assert consultation.slot_id is None
    assert consultation.slot_reserved_until is None
    assert slot.status == "free"
    assert slot.held_by_user_id is None
    assert slot.consultation_id is None
    assert slot.hold_expires_at is None
    assert case.status == CaseStatus.M2_SLOT_PENDING
    assert (
        await session.execute(select(Payment).where(Payment.case_id == case.id))
    ).scalars().all() == []


@pytest.mark.asyncio
async def test_expired_no_payment_hold_restores_consultation_and_case_to_slot_selection(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(booking_module, "payments_disabled", lambda: True)

    async with database(tmp_path) as factory:
        async with factory() as session:
            user, case, consultation, slot = await seed_expired_hold(
                session,
                telegram_id=983205,
                case_number="M2-EXPIRED-NOPAY-V36",
            )

            with pytest.raises(SlotUnavailableError, match="Резерв времени истёк"):
                await ConsultationNoPaymentBookingService(session).confirm(
                    case=case,
                    client_id=user.id,
                )

            # The Telegram handler commits this cleanup branch. The domain
            # service now also returns the case to M2_SLOT_PENDING before raise.
            await session.commit()
            await assert_slot_selection_restored(
                session,
                case=case,
                consultation=consultation,
                slot=slot,
            )


@pytest.mark.asyncio
async def test_expired_online_payment_hold_restores_case_before_payment_creation(tmp_path):
    async with database(tmp_path) as factory:
        async with factory() as session:
            _user, case, consultation, slot = await seed_expired_hold(
                session,
                telegram_id=983206,
                case_number="M2-EXPIRED-ONLINE-V36",
            )

            with pytest.raises(SlotUnavailableError, match="Резерв времени истёк"):
                await PaymentService(session).get_or_create_payment(
                    case=case,
                    payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
                )

            await session.commit()
            await assert_slot_selection_restored(
                session,
                case=case,
                consultation=consultation,
                slot=slot,
            )


def test_both_telegram_m2_paths_commit_slot_unavailable_cleanup():
    source = Path("app/bot/screens/payments.py").read_text(encoding="utf-8")

    assert source.count("except SlotUnavailableError as error:") >= 2
    for fragment in source.split("except SlotUnavailableError as error:")[1:3]:
        handled = fragment.split("except ", 1)[0]
        assert "await db.commit()" in handled
        assert "consult_booking_start" in handled
