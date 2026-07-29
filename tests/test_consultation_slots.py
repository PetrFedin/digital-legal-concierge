from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.slot_service import SlotService, SlotUnavailableError
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.user import User


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def create_database(tmp_path, name: str):
    database_path = tmp_path / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, session_factory


async def create_booking_context(session, *, suffix: str = "1"):
    lawyer = Lawyer(
        full_name=f"Тестовый юрист {suffix}",
        is_active=True,
        workload_limit=10,
    )
    first_user = User(
        telegram_id=100000 + int(suffix) * 10 + 1,
        full_name=f"Клиент {suffix}-1",
    )
    second_user = User(
        telegram_id=100000 + int(suffix) * 10 + 2,
        full_name=f"Клиент {suffix}-2",
    )
    session.add_all([lawyer, first_user, second_user])
    await session.flush()

    first_case = Case(
        case_number=f"TEST-SLOT-{suffix}-1",
        client_id=first_user.id,
        route="M2",
        status="M2_SLOT_PENDING",
        title="Первая тестовая консультация",
    )
    second_case = Case(
        case_number=f"TEST-SLOT-{suffix}-2",
        client_id=second_user.id,
        route="M2",
        status="M2_SLOT_PENDING",
        title="Вторая тестовая консультация",
    )
    session.add_all([first_case, second_case])
    await session.flush()

    first_consultation = Consultation(
        case_id=first_case.id,
        status=ConsultationStatus.SLOT_PENDING,
    )
    second_consultation = Consultation(
        case_id=second_case.id,
        status=ConsultationStatus.SLOT_PENDING,
    )
    session.add_all([first_consultation, second_consultation])
    await session.flush()

    return {
        "lawyer": lawyer,
        "first_user": first_user,
        "second_user": second_user,
        "first_case": first_case,
        "second_case": second_case,
        "first_consultation": first_consultation,
        "second_consultation": second_consultation,
    }


@pytest.mark.asyncio
async def test_only_one_client_can_hold_the_same_slot_for_ten_minutes(tmp_path):
    engine, session_factory = await create_database(tmp_path, "slots.db")

    async with session_factory() as session:
        context = await create_booking_context(session, suffix="1")
        starts_at = datetime.now(timezone.utc) + timedelta(days=1)
        slot = ConsultationSlot(
            lawyer_id=context["lawyer"].id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            status="available",
        )
        session.add(slot)
        await session.commit()
        slot_id = slot.id
        first_user_id = context["first_user"].id
        second_user_id = context["second_user"].id
        first_consultation_id = context["first_consultation"].id
        second_consultation_id = context["second_consultation"].id

    before_hold = datetime.now(timezone.utc)
    async with session_factory() as first, session_factory() as second:
        first_slot = await SlotService(first).hold_slot(
            slot_id,
            user_id=first_user_id,
            consultation_id=first_consultation_id,
        )
        await first.commit()
        assert first_slot.status == "held"
        assert first_slot.hold_expires_at is not None
        hold_seconds = (
            as_utc(first_slot.hold_expires_at) - before_hold
        ).total_seconds()
        assert 9 * 60 <= hold_seconds <= 10 * 60 + 5

        with pytest.raises(SlotUnavailableError):
            await SlotService(second).hold_slot(
                slot_id,
                user_id=second_user_id,
                consultation_id=second_consultation_id,
            )
        await second.rollback()

    async with session_factory() as session:
        slot = await SlotService(session).get_slot(slot_id)
        assert slot is not None
        assert slot.status == "held"
        assert slot.held_by_user_id == first_user_id
        assert slot.consultation_id == first_consultation_id

    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_hold_releases_slot_and_resets_consultation(tmp_path):
    engine, session_factory = await create_database(tmp_path, "expired.db")

    async with session_factory() as session:
        context = await create_booking_context(session, suffix="2")
        consultation = context["first_consultation"]
        starts_at = datetime.now(timezone.utc) + timedelta(days=1)
        slot = ConsultationSlot(
            lawyer_id=context["lawyer"].id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            status="held",
            held_by_user_id=context["first_user"].id,
            consultation_id=consultation.id,
            hold_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        session.add(slot)
        await session.flush()
        consultation.slot_id = slot.id
        consultation.lawyer_id = context["lawyer"].id
        consultation.scheduled_at = starts_at
        consultation.status = ConsultationStatus.PAYMENT_PENDING
        await session.commit()
        slot_id = slot.id
        consultation_id = consultation.id

    async with session_factory() as session:
        released = await SlotService(session).release_expired_holds()
        await session.commit()
        assert released == 1

    async with session_factory() as session:
        slot = await session.get(ConsultationSlot, slot_id)
        consultation = await session.get(Consultation, consultation_id)
        assert slot is not None
        assert consultation is not None
        assert slot.status == "available"
        assert slot.hold_expires_at is None
        assert slot.held_by_user_id is None
        assert slot.consultation_id is None
        assert consultation.status == ConsultationStatus.SLOT_PENDING
        assert consultation.slot_id is None
        assert consultation.lawyer_id is None
        assert consultation.scheduled_at is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_can_create_two_non_overlapping_test_slots(tmp_path):
    engine, session_factory = await create_database(tmp_path, "test-slots.db")

    async with session_factory() as session:
        lawyer = Lawyer(
            full_name="Юрист для тестовых слотов",
            is_active=True,
            workload_limit=10,
        )
        session.add(lawyer)
        await session.flush()

        slots = await SlotService(session).create_test_slots(
            lawyer_id=lawyer.id,
            count=2,
            duration_minutes=60,
        )
        await session.commit()

        assert len(slots) == 2
        assert all(slot.status == "available" for slot in slots)
        assert all(slot.note == "Тестовый слот из админки" for slot in slots)
        assert as_utc(slots[0].ends_at) <= as_utc(slots[1].starts_at)
        assert as_utc(slots[0].starts_at) > datetime.now(timezone.utc)

    await engine.dispose()
