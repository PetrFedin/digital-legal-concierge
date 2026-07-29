from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cases.assignment_service import CaseAssignmentService
from app.domain.cases.case_service import CaseService
from app.domain.consultations.consultation_service import (
    ConsultationService,
    ConsultationSlotError,
)
from app.domain.consultations.reservation_service import (
    ConsultationReservationService,
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
async def capacity_reservation_db(tmp_path):
    database_path = tmp_path / "lawyer-capacity-reservations.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def seed_client_case(session, *, user_suffix: int, lawyer: Lawyer, hour: int):
    user = User(
        telegram_id=998_000 + user_suffix,
        telegram_username=f"capacity_reservation_{user_suffix}",
        full_name=f"Клиент {user_suffix}",
    )
    session.add(user)
    await session.flush()
    case = Case(
        case_number=f"CAPACITY-RESERVATION-{user_suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_SLOT_PENDING.value,
        title="Юридическая консультация",
        next_action="Выберите время консультации",
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
    starts_at = datetime.now(timezone.utc) + timedelta(days=2, hours=hour)
    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=45),
        status="available",
    )
    session.add(slot)
    await session.flush()
    return user, case, consultation, slot


def snapshot_for(rows, lawyer_id: int):
    return next(item for item in rows if item["id"] == lawyer_id)


@pytest.mark.asyncio
async def test_hold_consumes_capacity_until_reservation_is_released(
    capacity_reservation_db,
):
    async with capacity_reservation_db() as session:
        lawyer = Lawyer(
            full_name="Юрист с одним местом",
            specialization="Споры по ДДУ",
            workload_limit=1,
            is_active=True,
        )
        session.add(lawyer)
        await session.flush()
        first = await seed_client_case(
            session,
            user_suffix=1,
            lawyer=lawyer,
            hour=1,
        )
        second = await seed_client_case(
            session,
            user_suffix=2,
            lawyer=lawyer,
            hour=2,
        )

        first_user, first_case, first_consultation, first_slot = first
        second_user, second_case, second_consultation, second_slot = second
        await ConsultationService(session).reserve_pre_payment_slot(
            consultation=first_consultation,
            case=first_case,
            client_id=first_user.id,
            slot_id=first_slot.id,
            actor_type="client",
            source="test",
        )
        await session.commit()

        capacity = snapshot_for(
            await CaseAssignmentService(session).list_active_lawyers(),
            lawyer.id,
        )
        assert capacity["current_workload"] == 1
        assert capacity["available_capacity"] == 0
        assert capacity["is_available"] is False

        with pytest.raises(ConsultationSlotError, match="Слот стал недоступен"):
            await ConsultationService(session).reserve_pre_payment_slot(
                consultation=second_consultation,
                case=second_case,
                client_id=second_user.id,
                slot_id=second_slot.id,
                actor_type="client",
                source="test",
            )
        await session.rollback()

        assert second_consultation.status == ConsultationStatus.SLOT_PENDING.value
        assert second_consultation.slot_id is None
        assert second_slot.status == "available"

        # Converting the same reserved case into an assigned case must not count
        # it twice because capacity is a union by (lawyer_id, case_id).
        await CaseService(session).assign_lawyer(
            case=first_case,
            lawyer_id=lawyer.id,
            actor_id=7001,
        )
        await session.commit()
        capacity = snapshot_for(
            await CaseAssignmentService(session).list_active_lawyers(),
            lawyer.id,
        )
        assert capacity["current_workload"] == 1

        await ConsultationReservationService(session).release_before_payment(
            consultation=first_consultation,
            case=first_case,
            client_id=first_user.id,
            actor_id=first_user.id,
            source="test",
        )
        first_case.assigned_lawyer_id = None
        await session.commit()

        capacity = snapshot_for(
            await CaseAssignmentService(session).list_active_lawyers(),
            lawyer.id,
        )
        assert capacity["current_workload"] == 0
        assert capacity["available_capacity"] == 1
        assert capacity["is_available"] is True

        await ConsultationService(session).reserve_pre_payment_slot(
            consultation=second_consultation,
            case=second_case,
            client_id=second_user.id,
            slot_id=second_slot.id,
            actor_type="client",
            source="test",
        )
        await session.commit()

        assert second_consultation.status == ConsultationStatus.SLOT_RESERVED.value
        assert second_consultation.lawyer_id == lawyer.id
        assert second_slot.status == "held"
