from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.slot_service import SlotService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.user import User
from app.scheduler.jobs import SchedulerJobs


@pytest.fixture
async def linkage_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'slot-case-linkage.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_hold_derives_case_and_release_clears_case_link(linkage_db):
    async with linkage_db() as session:
        now = datetime.now(timezone.utc)
        user = User(
            telegram_id=999_980,
            full_name="Клиент связи слота",
        )
        lawyer = Lawyer(
            full_name="Юрист связи слота",
            is_active=True,
        )
        session.add_all([user, lawyer])
        await session.flush()
        case = Case(
            case_number="SLOT-CASE-LINK",
            client_id=user.id,
            route=RouteCode.M2.value,
            status=CaseStatus.M2_SLOT_PENDING.value,
            title="Юридическая консультация",
        )
        session.add(case)
        await session.flush()
        consultation = Consultation(
            case_id=case.id,
            status=ConsultationStatus.SLOT_PENDING.value,
        )
        session.add(consultation)
        await session.flush()
        slot = ConsultationSlot(
            lawyer_id=lawyer.id,
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=1, minutes=45),
            status="available",
        )
        session.add(slot)
        await session.commit()

        held = await SlotService(session).hold_slot(
            slot.id,
            user.id,
            consultation.id,
        )
        held.hold_expires_at = now + timedelta(minutes=4)
        consultation.slot_id = held.id
        consultation.lawyer_id = lawyer.id
        consultation.scheduled_at = held.starts_at
        consultation.status = ConsultationStatus.PAYMENT_PENDING.value
        case.status = CaseStatus.M2_PAYMENT_PENDING.value
        await session.commit()

        assert held.case_id == case.id
        assert await SchedulerJobs(
            session
        ).check_expiring_consultation_holds() == 1

        released = await SlotService(session).release_slot(
            held.id,
            consultation.id,
        )
        await session.commit()

        assert released is not None
        assert released.status == "available"
        assert released.case_id is None
        assert released.consultation_id is None
        assert released.held_by_user_id is None
        assert released.hold_expires_at is None


@pytest.mark.asyncio
async def test_idempotent_hold_repairs_missing_case_link(linkage_db):
    async with linkage_db() as session:
        now = datetime.now(timezone.utc)
        user = User(telegram_id=999_981, full_name="Клиент repair")
        lawyer = Lawyer(full_name="Юрист repair", is_active=True)
        session.add_all([user, lawyer])
        await session.flush()
        case = Case(
            case_number="SLOT-CASE-REPAIR",
            client_id=user.id,
            route=RouteCode.M2.value,
            status=CaseStatus.M2_PAYMENT_PENDING.value,
        )
        session.add(case)
        await session.flush()
        consultation = Consultation(
            case_id=case.id,
            status=ConsultationStatus.PAYMENT_PENDING.value,
        )
        session.add(consultation)
        await session.flush()
        slot = ConsultationSlot(
            lawyer_id=lawyer.id,
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=1, minutes=45),
            status="held",
            case_id=None,
            hold_expires_at=now + timedelta(minutes=10),
            held_by_user_id=user.id,
            consultation_id=consultation.id,
        )
        session.add(slot)
        await session.commit()

        repaired = await SlotService(session).hold_slot(
            slot.id,
            user.id,
            consultation.id,
        )
        await session.commit()

        assert repaired.id == slot.id
        assert repaired.case_id == case.id
