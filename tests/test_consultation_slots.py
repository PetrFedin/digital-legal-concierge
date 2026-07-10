from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.slot_service import SlotService, SlotUnavailableError
from app.models import Base
from app.models.consultation_slot import ConsultationSlot


@pytest.mark.asyncio
async def test_only_one_client_can_hold_the_same_slot(tmp_path):
    database_path = tmp_path / "slots.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    starts_at = datetime.now(timezone.utc) + timedelta(days=1)
    async with session_factory() as session:
        slot = ConsultationSlot(
            lawyer_id=1,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            status="available",
        )
        session.add(slot)
        await session.commit()
        slot_id = slot.id

    async with session_factory() as first, session_factory() as second:
        first_slot = await SlotService(first).hold_slot(slot_id, user_id=101, consultation_id=201)
        await first.commit()
        assert first_slot.status == "held"

        with pytest.raises(SlotUnavailableError):
            await SlotService(second).hold_slot(slot_id, user_id=102, consultation_id=202)
        await second.rollback()

    async with session_factory() as session:
        slot = await SlotService(session).get_slot(slot_id)
        assert slot is not None
        assert slot.status == "held"
        assert slot.held_by_user_id == 101
        assert slot.consultation_id == 201

    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_hold_returns_to_available(tmp_path):
    database_path = tmp_path / "expired.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    starts_at = datetime.now(timezone.utc) + timedelta(days=1)
    async with session_factory() as session:
        slot = ConsultationSlot(
            lawyer_id=1,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            status="held",
            held_by_user_id=101,
            consultation_id=201,
            hold_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        session.add(slot)
        await session.commit()
        slot_id = slot.id

    async with session_factory() as session:
        slots = await SlotService(session).get_available_slots()
        await session.commit()
        assert any(slot.id == slot_id for slot in slots)

    await engine.dispose()
