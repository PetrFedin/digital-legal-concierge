from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.slot_service import SlotService, SlotUnavailableError
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.user import User


@pytest.fixture
async def booking_link_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'booking-link-repair.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def seed_slot(session, *, suffix: int, status: str):
    now = datetime.now(timezone.utc)
    user = User(
        telegram_id=1_001_000 + suffix,
        full_name=f"Клиент legacy {suffix}",
    )
    lawyer = Lawyer(
        full_name=f"Юрист legacy {suffix}",
        is_active=True,
    )
    session.add_all([user, lawyer])
    await session.flush()
    case = Case(
        case_number=f"LEGACY-BOOKING-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_PAYMENT_PENDING.value,
        title="Юридическая консультация",
    )
    session.add(case)
    await session.flush()
    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        status=(
            ConsultationStatus.PAYMENT_PENDING.value
            if status == "held"
            else ConsultationStatus.BOOKED.value
        ),
        scheduled_at=now + timedelta(days=2),
    )
    session.add(consultation)
    await session.flush()
    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=2, minutes=45),
        status=status,
        case_id=None,
        hold_expires_at=(
            now + timedelta(minutes=10) if status == "held" else None
        ),
        held_by_user_id=user.id,
        consultation_id=consultation.id,
    )
    session.add(slot)
    await session.flush()
    consultation.slot_id = slot.id
    await session.commit()
    return case, consultation, slot


@pytest.mark.asyncio
async def test_confirm_booking_sets_case_link_for_legacy_held_slot(
    booking_link_db,
):
    async with booking_link_db() as session:
        case, consultation, slot = await seed_slot(
            session,
            suffix=1,
            status="held",
        )

        confirmed = await SlotService(session).confirm_booking(
            slot.id,
            consultation.id,
        )
        await session.commit()

        assert confirmed.status == "booked"
        assert confirmed.case_id == case.id
        assert confirmed.consultation_id == consultation.id
        assert confirmed.hold_expires_at is None


@pytest.mark.asyncio
async def test_idempotent_confirm_repairs_case_link_for_booked_slot(
    booking_link_db,
):
    async with booking_link_db() as session:
        case, consultation, slot = await seed_slot(
            session,
            suffix=2,
            status="booked",
        )

        first = await SlotService(session).confirm_booking(
            slot.id,
            consultation.id,
        )
        second = await SlotService(session).confirm_booking(
            slot.id,
            consultation.id,
        )
        await session.commit()

        assert first.id == second.id == slot.id
        assert second.status == "booked"
        assert second.case_id == case.id
        assert second.consultation_id == consultation.id


@pytest.mark.asyncio
async def test_confirm_booking_rejects_unknown_consultation(booking_link_db):
    async with booking_link_db() as session:
        _, consultation, slot = await seed_slot(
            session,
            suffix=3,
            status="held",
        )

        with pytest.raises(SlotUnavailableError, match="не связан"):
            await SlotService(session).confirm_booking(
                slot.id,
                consultation.id + 1000,
            )
