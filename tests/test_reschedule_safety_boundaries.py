from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.consultation_service import (
    ActiveConsultationConflictError,
    ConsultationSlotError,
)
from app.domain.consultations.reschedule_service import ConsultationRescheduleService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.user import User


@pytest.fixture
async def reschedule_safety_db(tmp_path):
    database_path = tmp_path / "reschedule-safety-boundaries.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def seed_booked(
    session,
    *,
    suffix: int,
    route: str = RouteCode.M2.value,
):
    starts_at = datetime.now(timezone.utc) + timedelta(days=3)
    user = User(
        telegram_id=1_010_000 + suffix,
        telegram_username=f"reschedule_safety_{suffix}",
        full_name=f"Клиент {suffix}",
    )
    lawyer = Lawyer(
        full_name=f"Юрист {suffix}",
        workload_limit=10,
        is_active=True,
    )
    session.add_all([user, lawyer])
    await session.flush()
    case = Case(
        case_number=f"RESCHEDULE-SAFETY-{suffix}",
        client_id=user.id,
        route=route,
        status=CaseStatus.M2_CONSULTATION_BOOKED.value,
        title="Оплаченная консультация",
        next_action="Ожидайте консультации",
        assigned_lawyer_id=lawyer.id,
    )
    session.add(case)
    await session.flush()
    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        status=ConsultationStatus.BOOKED.value,
        scheduled_at=starts_at,
        consultation_type="online",
    )
    session.add(consultation)
    await session.flush()
    old_slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=45),
        status="booked",
        held_by_user_id=user.id,
        consultation_id=consultation.id,
    )
    session.add(old_slot)
    await session.flush()
    consultation.slot_id = old_slot.id
    await session.flush()
    return user, lawyer, case, consultation, old_slot


@pytest.mark.asyncio
async def test_reschedule_rejects_changed_paid_duration(reschedule_safety_db):
    async with reschedule_safety_db() as session:
        user, lawyer, case, consultation, old_slot = await seed_booked(
            session,
            suffix=1,
        )
        replacement = ConsultationSlot(
            lawyer_id=lawyer.id,
            starts_at=old_slot.starts_at + timedelta(days=1),
            ends_at=old_slot.starts_at + timedelta(days=1, minutes=60),
            status="available",
        )
        session.add(replacement)
        await session.flush()

        with pytest.raises(ConsultationSlotError, match="длительности"):
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
        assert replacement.status == "available"


@pytest.mark.asyncio
async def test_reschedule_rejects_other_client_booking_overlap(reschedule_safety_db):
    async with reschedule_safety_db() as session:
        user, lawyer, case, consultation, old_slot = await seed_booked(
            session,
            suffix=2,
        )
        new_start = old_slot.starts_at + timedelta(days=1)
        replacement = ConsultationSlot(
            lawyer_id=lawyer.id,
            starts_at=new_start,
            ends_at=new_start + timedelta(minutes=45),
            status="available",
        )
        session.add(replacement)
        await session.flush()

        other_lawyer = Lawyer(
            full_name="Другой юрист клиента",
            workload_limit=10,
            is_active=True,
        )
        session.add(other_lawyer)
        await session.flush()
        other_case = Case(
            case_number="CLIENT-OVERLAP-OTHER",
            client_id=user.id,
            route=RouteCode.M2.value,
            status=CaseStatus.M2_CONSULTATION_BOOKED.value,
            title="Другая консультация клиента",
        )
        session.add(other_case)
        await session.flush()
        other_consultation = Consultation(
            case_id=other_case.id,
            lawyer_id=other_lawyer.id,
            status=ConsultationStatus.BOOKED.value,
            scheduled_at=new_start,
        )
        session.add(other_consultation)
        await session.flush()
        other_slot = ConsultationSlot(
            lawyer_id=other_lawyer.id,
            starts_at=new_start,
            ends_at=new_start + timedelta(minutes=45),
            status="booked",
            held_by_user_id=user.id,
            consultation_id=other_consultation.id,
        )
        session.add(other_slot)
        await session.flush()
        other_consultation.slot_id = other_slot.id
        await session.flush()

        with pytest.raises(ConsultationSlotError, match="другой вашей консультацией"):
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
        assert replacement.status == "available"


@pytest.mark.asyncio
async def test_reschedule_rejects_non_m2_case(reschedule_safety_db):
    async with reschedule_safety_db() as session:
        user, lawyer, case, consultation, old_slot = await seed_booked(
            session,
            suffix=3,
            route=RouteCode.M1.value,
        )
        replacement = ConsultationSlot(
            lawyer_id=lawyer.id,
            starts_at=old_slot.starts_at + timedelta(days=1),
            ends_at=old_slot.ends_at + timedelta(days=1),
            status="available",
        )
        session.add(replacement)
        await session.flush()

        with pytest.raises(ActiveConsultationConflictError, match="маршрута"):
            await ConsultationRescheduleService(session).reschedule(
                consultation=consultation,
                case=case,
                client_id=user.id,
                new_slot_id=replacement.id,
                actor_id=user.id,
            )

        assert consultation.slot_id == old_slot.id
        assert old_slot.status == "booked"
        assert replacement.status == "available"


def test_m2_management_router_precedes_legacy_consultation_handlers():
    entry = Path("app/bot/screens/consultation_entry.py").read_text(encoding="utf-8")
    bot = Path("app/bot/bot.py").read_text(encoding="utf-8")

    assert entry.index("include_router(reschedule_router)") < entry.index(
        "include_router(cancellation_router)"
    )
    assert bot.index("consultation_entry.router") < bot.index(
        "consultations.router"
    )
