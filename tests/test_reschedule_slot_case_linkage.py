from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.consultation_service import ConsultationSlotError
from app.domain.consultations.reschedule_service import (
    ConsultationRescheduleService,
)
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.user import User


@pytest.fixture
async def reschedule_link_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'reschedule-link.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def seed_reschedule(session, *, suffix: int):
    now = datetime.now(timezone.utc)
    user = User(
        telegram_id=1_002_000 + suffix,
        full_name=f"Клиент переноса связи {suffix}",
    )
    lawyer = Lawyer(
        full_name=f"Юрист переноса связи {suffix}",
        is_active=True,
    )
    session.add_all([user, lawyer])
    await session.flush()
    case = Case(
        case_number=f"RESCHEDULE-LINK-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_CONSULTATION_BOOKED.value,
        title="Юридическая консультация",
        assigned_lawyer_id=lawyer.id,
    )
    session.add(case)
    await session.flush()
    starts_at = now + timedelta(days=2)
    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        status=ConsultationStatus.BOOKED.value,
        scheduled_at=starts_at,
    )
    session.add(consultation)
    await session.flush()
    old_slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        case_id=case.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=45),
        status="booked",
        held_by_user_id=user.id,
        consultation_id=consultation.id,
    )
    replacement = ConsultationSlot(
        lawyer_id=lawyer.id,
        case_id=None,
        starts_at=starts_at + timedelta(days=1),
        ends_at=starts_at + timedelta(days=1, minutes=45),
        status="available",
    )
    session.add_all([old_slot, replacement])
    await session.flush()
    consultation.slot_id = old_slot.id
    await session.commit()
    return user, case, consultation, old_slot, replacement


@pytest.mark.asyncio
async def test_successful_reschedule_moves_case_link_to_new_slot(
    reschedule_link_db,
):
    async with reschedule_link_db() as session:
        user, case, consultation, old_slot, replacement = await seed_reschedule(
            session,
            suffix=1,
        )

        updated, new_slot = await ConsultationRescheduleService(session).reschedule(
            consultation=consultation,
            case=case,
            client_id=user.id,
            new_slot_id=replacement.id,
            actor_id=user.id,
        )
        await session.commit()

        await session.refresh(old_slot)
        await session.refresh(replacement)
        assert updated.slot_id == replacement.id
        assert new_slot.id == replacement.id
        assert old_slot.status == "available"
        assert old_slot.case_id is None
        assert old_slot.consultation_id is None
        assert replacement.status == "booked"
        assert replacement.case_id == case.id
        assert replacement.consultation_id == consultation.id


@pytest.mark.asyncio
async def test_failed_reschedule_preserves_original_case_link(
    reschedule_link_db,
):
    async with reschedule_link_db() as session:
        user, case, consultation, old_slot, replacement = await seed_reschedule(
            session,
            suffix=2,
        )
        replacement.status = "booked"
        await session.commit()

        with pytest.raises(ConsultationSlotError, match="прежняя запись сохранена"):
            await ConsultationRescheduleService(session).reschedule(
                consultation=consultation,
                case=case,
                client_id=user.id,
                new_slot_id=replacement.id,
                actor_id=user.id,
            )
        await session.rollback()

        await session.refresh(consultation)
        await session.refresh(old_slot)
        await session.refresh(replacement)
        assert consultation.slot_id == old_slot.id
        assert old_slot.status == "booked"
        assert old_slot.case_id == case.id
        assert old_slot.consultation_id == consultation.id
        assert replacement.status == "booked"
        assert replacement.case_id is None


@pytest.mark.asyncio
async def test_same_slot_reschedule_repairs_missing_case_link(
    reschedule_link_db,
):
    async with reschedule_link_db() as session:
        user, case, consultation, old_slot, _ = await seed_reschedule(
            session,
            suffix=3,
        )
        old_slot.case_id = None
        await session.commit()

        _, selected = await ConsultationRescheduleService(session).reschedule(
            consultation=consultation,
            case=case,
            client_id=user.id,
            new_slot_id=old_slot.id,
            actor_id=user.id,
        )
        await session.commit()

        assert selected.id == old_slot.id
        assert selected.status == "booked"
        assert selected.case_id == case.id
        assert consultation.slot_id == old_slot.id
