from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError
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
async def slot_overlap_db(tmp_path):
    database_path = tmp_path / "slot-overlap-integrity.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def seed_context(
    session,
    *,
    suffix: int,
    lawyer: Lawyer,
    starts_at: datetime,
    route: str = RouteCode.M2.value,
):
    user = User(
        telegram_id=1_050_000 + suffix,
        telegram_username=f"slot_overlap_{suffix}",
        full_name=f"Клиент {suffix}",
    )
    session.add(user)
    await session.flush()
    case = Case(
        case_number=f"SLOT-OVERLAP-{suffix}",
        client_id=user.id,
        route=route,
        status=(
            CaseStatus.M2_SLOT_PENDING.value
            if route == RouteCode.M2.value
            else CaseStatus.M1_LAWYER_REVIEW.value
        ),
        title="Консультация",
    )
    session.add(case)
    await session.flush()
    consultation = Consultation(
        case_id=case.id,
        status=ConsultationStatus.SLOT_PENDING.value,
        consultation_type="online",
    )
    session.add(consultation)
    await session.flush()
    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=45),
        status="available",
    )
    session.add(slot)
    await session.flush()
    return user, case, consultation, slot


@pytest.mark.asyncio
async def test_same_lawyer_overlapping_hold_is_rejected(slot_overlap_db):
    async with slot_overlap_db() as session:
        starts_at = datetime.now(timezone.utc) + timedelta(days=2)
        lawyer = Lawyer(
            full_name="Юрист overlap",
            workload_limit=10,
            is_active=True,
        )
        session.add(lawyer)
        await session.flush()
        first = await seed_context(
            session,
            suffix=1,
            lawyer=lawyer,
            starts_at=starts_at,
        )
        second = await seed_context(
            session,
            suffix=2,
            lawyer=lawyer,
            starts_at=starts_at + timedelta(minutes=15),
        )
        first_user, _, first_consultation, first_slot = first
        second_user, _, second_consultation, second_slot = second
        service = SlotService(session)

        await service.hold_slot(
            first_slot.id,
            first_user.id,
            first_consultation.id,
        )
        await session.flush()

        with pytest.raises(SlotUnavailableError, match="пересекается"):
            await service.hold_slot(
                second_slot.id,
                second_user.id,
                second_consultation.id,
            )

        await session.refresh(first_slot)
        await session.refresh(second_slot)
        assert first_slot.status == "held"
        assert second_slot.status == "available"


@pytest.mark.asyncio
async def test_same_client_overlap_across_different_lawyers_is_rejected(
    slot_overlap_db,
):
    async with slot_overlap_db() as session:
        starts_at = datetime.now(timezone.utc) + timedelta(days=3)
        first_lawyer = Lawyer(
            full_name="Первый юрист",
            workload_limit=10,
            is_active=True,
        )
        second_lawyer = Lawyer(
            full_name="Второй юрист",
            workload_limit=10,
            is_active=True,
        )
        user = User(
            telegram_id=1_050_100,
            telegram_username="slot_overlap_same_client",
            full_name="Один клиент",
        )
        session.add_all([first_lawyer, second_lawyer, user])
        await session.flush()

        first_case = Case(
            case_number="CLIENT-OVERLAP-M2",
            client_id=user.id,
            route=RouteCode.M2.value,
            status=CaseStatus.M2_SLOT_PENDING.value,
            title="M2 консультация",
        )
        second_case = Case(
            case_number="CLIENT-OVERLAP-M1",
            client_id=user.id,
            route=RouteCode.M1.value,
            status=CaseStatus.M1_LAWYER_REVIEW.value,
            title="M1 консультация",
        )
        session.add_all([first_case, second_case])
        await session.flush()
        first_consultation = Consultation(
            case_id=first_case.id,
            status=ConsultationStatus.SLOT_PENDING.value,
        )
        second_consultation = Consultation(
            case_id=second_case.id,
            status=ConsultationStatus.SLOT_PENDING.value,
        )
        session.add_all([first_consultation, second_consultation])
        await session.flush()
        first_slot = ConsultationSlot(
            lawyer_id=first_lawyer.id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=45),
            status="available",
        )
        second_slot = ConsultationSlot(
            lawyer_id=second_lawyer.id,
            starts_at=starts_at + timedelta(minutes=10),
            ends_at=starts_at + timedelta(minutes=55),
            status="available",
        )
        session.add_all([first_slot, second_slot])
        await session.flush()
        service = SlotService(session)

        await service.hold_slot(
            first_slot.id,
            user.id,
            first_consultation.id,
        )

        with pytest.raises(SlotUnavailableError, match="пересекается"):
            await service.hold_slot(
                second_slot.id,
                user.id,
                second_consultation.id,
            )

        await session.refresh(second_slot)
        assert second_slot.status == "available"


@pytest.mark.asyncio
async def test_non_overlapping_slots_can_be_held(slot_overlap_db):
    async with slot_overlap_db() as session:
        starts_at = datetime.now(timezone.utc) + timedelta(days=4)
        lawyer = Lawyer(
            full_name="Юрист без overlap",
            workload_limit=10,
            is_active=True,
        )
        session.add(lawyer)
        await session.flush()
        first = await seed_context(
            session,
            suffix=20,
            lawyer=lawyer,
            starts_at=starts_at,
        )
        second = await seed_context(
            session,
            suffix=21,
            lawyer=lawyer,
            starts_at=starts_at + timedelta(minutes=45),
        )
        service = SlotService(session)

        await service.hold_slot(first[3].id, first[0].id, first[2].id)
        await service.hold_slot(second[3].id, second[0].id, second[2].id)
        await session.flush()

        assert first[3].status == "held"
        assert second[3].status == "held"


@pytest.mark.asyncio
async def test_database_rejects_non_positive_slot_interval(slot_overlap_db):
    async with slot_overlap_db() as session:
        lawyer = Lawyer(
            full_name="Юрист invalid interval",
            workload_limit=10,
            is_active=True,
        )
        session.add(lawyer)
        await session.flush()
        starts_at = datetime.now(timezone.utc) + timedelta(days=5)
        session.add(
            ConsultationSlot(
                lawyer_id=lawyer.id,
                starts_at=starts_at,
                ends_at=starts_at,
                status="available",
            )
        )

        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.asyncio
async def test_database_rejects_same_active_start_for_lawyer(slot_overlap_db):
    async with slot_overlap_db() as session:
        lawyer = Lawyer(
            full_name="Юрист duplicate start",
            workload_limit=10,
            is_active=True,
        )
        first_user = User(telegram_id=1_050_200, full_name="Первый")
        second_user = User(telegram_id=1_050_201, full_name="Второй")
        session.add_all([lawyer, first_user, second_user])
        await session.flush()
        starts_at = datetime.now(timezone.utc) + timedelta(days=6)
        session.add_all(
            [
                ConsultationSlot(
                    lawyer_id=lawyer.id,
                    starts_at=starts_at,
                    ends_at=starts_at + timedelta(minutes=45),
                    status="held",
                    held_by_user_id=first_user.id,
                    hold_expires_at=datetime.now(timezone.utc)
                    + timedelta(minutes=20),
                ),
                ConsultationSlot(
                    lawyer_id=lawyer.id,
                    starts_at=starts_at,
                    ends_at=starts_at + timedelta(minutes=30),
                    status="booked",
                    held_by_user_id=second_user.id,
                ),
            ]
        )

        with pytest.raises(IntegrityError):
            await session.flush()
