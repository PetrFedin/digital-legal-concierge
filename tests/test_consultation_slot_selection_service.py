from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.slot_selection_service import (
    ConsultationSlotSelectionError,
    ConsultationSlotSelectionService,
)
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.user import User


UTC = timezone.utc
NOW = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
async def selection_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'slot-selection.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield engine, sessions
    finally:
        await engine.dispose()


async def create_context(session, *, suffix: int = 1):
    user = User(
        telegram_id=996_000 + suffix,
        full_name=f"Клиент выбора {suffix}",
    )
    session.add(user)
    await session.flush()
    case = Case(
        case_number=f"SELECTION-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_SLOT_PENDING.value,
        title="Юридическая консультация",
        next_action="Выберите время",
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
    return user, case, consultation


async def create_lawyer_with_slot(
    session,
    *,
    suffix: int,
    starts_at: datetime,
    active: bool = True,
    workload_limit: int = 30,
    slot_status: str = "available",
):
    lawyer = Lawyer(
        full_name=f"Юрист {suffix:02d}",
        phone=f"+7-900-000-{suffix:04d}",
        email=f"internal-{suffix}@example.test",
        specialization=f"Направление {suffix}",
        is_active=active,
        workload_limit=workload_limit,
    )
    session.add(lawyer)
    await session.flush()
    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=45),
        status=slot_status,
    )
    session.add(slot)
    await session.flush()
    return lawyer, slot


@pytest.mark.asyncio
async def test_selection_returns_only_client_safe_sorted_slots(selection_db):
    _, sessions = selection_db
    async with sessions() as session:
        user, case, consultation = await create_context(session)
        later, later_slot = await create_lawyer_with_slot(
            session,
            suffix=2,
            starts_at=NOW + timedelta(days=3),
        )
        earlier, earlier_slot = await create_lawyer_with_slot(
            session,
            suffix=1,
            starts_at=NOW + timedelta(days=2),
        )
        await create_lawyer_with_slot(
            session,
            suffix=3,
            starts_at=NOW - timedelta(hours=1),
        )
        await create_lawyer_with_slot(
            session,
            suffix=4,
            starts_at=NOW + timedelta(days=4),
            active=False,
        )
        await create_lawyer_with_slot(
            session,
            suffix=5,
            starts_at=NOW + timedelta(days=5),
            slot_status="booked",
        )
        await session.commit()

        selection = await ConsultationSlotSelectionService(session).build_selection(
            client_id=user.id,
            case_id=case.id,
            consultation_id=consultation.id,
            now=NOW,
        )

        assert [item.reference for item in selection.slots] == [
            earlier_slot.id,
            later_slot.id,
        ]
        assert [item.reference for item in selection.lawyers] == [
            earlier.id,
            later.id,
        ]
        serialized = str(asdict(selection))
        assert "internal-1@example.test" not in serialized
        assert "+7-900" not in serialized
        assert "workload_limit" not in serialized
        assert "current_workload" not in serialized
        assert "available_capacity" not in serialized


@pytest.mark.asyncio
async def test_full_lawyer_is_hidden_for_new_case(selection_db):
    _, sessions = selection_db
    async with sessions() as session:
        user, case, consultation = await create_context(session, suffix=2)
        full, _ = await create_lawyer_with_slot(
            session,
            suffix=10,
            starts_at=NOW + timedelta(days=2),
            workload_limit=1,
        )
        other_user = User(telegram_id=996_999, full_name="Другой клиент")
        session.add(other_user)
        await session.flush()
        session.add(
            Case(
                case_number="FULL-LAWYER-CASE",
                client_id=other_user.id,
                route=RouteCode.M1.value,
                status=CaseStatus.M1_LAWYER_REVIEW.value,
                assigned_lawyer_id=full.id,
            )
        )
        await session.commit()

        selection = await ConsultationSlotSelectionService(session).build_selection(
            client_id=user.id,
            case_id=case.id,
            consultation_id=consultation.id,
            now=NOW,
        )

        assert selection.slots == ()
        assert selection.lawyers == ()


@pytest.mark.asyncio
async def test_assigned_lawyer_remains_available_at_capacity(selection_db):
    _, sessions = selection_db
    async with sessions() as session:
        user, case, consultation = await create_context(session, suffix=3)
        assigned, slot = await create_lawyer_with_slot(
            session,
            suffix=11,
            starts_at=NOW + timedelta(days=2),
            workload_limit=1,
        )
        case.assigned_lawyer_id = assigned.id
        await session.commit()

        selection = await ConsultationSlotSelectionService(session).build_selection(
            client_id=user.id,
            case_id=case.id,
            consultation_id=consultation.id,
            now=NOW,
        )

        assert selection.assigned_lawyer_reference == assigned.id
        assert [item.reference for item in selection.slots] == [slot.id]
        assert [item.reference for item in selection.lawyers] == [assigned.id]


@pytest.mark.asyncio
async def test_client_overlapping_booking_is_excluded(selection_db):
    _, sessions = selection_db
    async with sessions() as session:
        user, case, consultation = await create_context(session, suffix=4)
        lawyer, overlapping_slot = await create_lawyer_with_slot(
            session,
            suffix=12,
            starts_at=NOW + timedelta(days=2),
        )
        free_slot = ConsultationSlot(
            lawyer_id=lawyer.id,
            starts_at=NOW + timedelta(days=2, hours=2),
            ends_at=NOW + timedelta(days=2, hours=3),
            status="available",
        )
        session.add(free_slot)

        other_case = Case(
            case_number="CLIENT-BUSY",
            client_id=user.id,
            route=RouteCode.M2.value,
            status=CaseStatus.M2_CONSULTATION_BOOKED.value,
        )
        session.add(other_case)
        await session.flush()
        busy_consultation = Consultation(
            case_id=other_case.id,
            lawyer_id=lawyer.id,
            status=ConsultationStatus.BOOKED.value,
            scheduled_at=overlapping_slot.starts_at,
        )
        session.add(busy_consultation)
        await session.flush()
        overlapping_slot.status = "booked"
        overlapping_slot.consultation_id = busy_consultation.id
        busy_consultation.slot_id = overlapping_slot.id
        await session.commit()

        selection = await ConsultationSlotSelectionService(session).build_selection(
            client_id=user.id,
            case_id=case.id,
            consultation_id=consultation.id,
            now=NOW,
        )

        assert [item.reference for item in selection.slots] == [free_slot.id]


@pytest.mark.asyncio
async def test_foreign_or_stale_context_is_rejected(selection_db):
    _, sessions = selection_db
    async with sessions() as session:
        user, case, consultation = await create_context(session, suffix=5)
        stranger = User(telegram_id=996_888, full_name="Чужой клиент")
        session.add(stranger)
        await session.commit()

        with pytest.raises(ConsultationSlotSelectionError):
            await ConsultationSlotSelectionService(session).build_selection(
                client_id=stranger.id,
                case_id=case.id,
                consultation_id=consultation.id,
                now=NOW,
            )
        case.status = CaseStatus.M2_PAYMENT_PENDING.value
        await session.flush()
        with pytest.raises(ConsultationSlotSelectionError):
            await ConsultationSlotSelectionService(session).build_selection(
                client_id=user.id,
                case_id=case.id,
                consultation_id=consultation.id,
                now=NOW,
            )


async def measured_query_count(engine, session, *, user, case, consultation):
    count = 0
    measuring = False

    def before_cursor_execute(
        connection,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ):
        nonlocal count
        if measuring:
            count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", before_cursor_execute)
    try:
        measuring = True
        await ConsultationSlotSelectionService(session).build_selection(
            client_id=user.id,
            case_id=case.id,
            consultation_id=consultation.id,
            now=NOW,
        )
        measuring = False
    finally:
        event.remove(
            engine.sync_engine,
            "before_cursor_execute",
            before_cursor_execute,
        )
    return count


@pytest.mark.asyncio
async def test_selection_query_count_does_not_grow_with_result_size(selection_db):
    engine, sessions = selection_db
    async with sessions() as session:
        user, case, consultation = await create_context(session, suffix=6)
        for suffix in range(20, 22):
            await create_lawyer_with_slot(
                session,
                suffix=suffix,
                starts_at=NOW + timedelta(days=2, minutes=suffix),
            )
        await session.commit()
        small_count = await measured_query_count(
            engine,
            session,
            user=user,
            case=case,
            consultation=consultation,
        )

        for suffix in range(22, 40):
            await create_lawyer_with_slot(
                session,
                suffix=suffix,
                starts_at=NOW + timedelta(days=3, minutes=suffix),
            )
        await session.commit()
        large_count = await measured_query_count(
            engine,
            session,
            user=user,
            case=case,
            consultation=consultation,
        )

        assert small_count == large_count == 4
