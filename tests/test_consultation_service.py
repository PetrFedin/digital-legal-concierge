from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.consultation_service import (
    ActiveConsultationConflictError,
    ConsultationDescriptionError,
    ConsultationService,
    ConsultationSlotError,
)
from app.domain.consultations.slot_service import SlotUnavailableError
from app.domain.consultations.state_machine import InvalidConsultationTransition
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.document import Document
from app.models.user import User


@pytest.fixture
async def consultation_db(tmp_path):
    database_path = tmp_path / "consultation-service.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        yield session_factory
    finally:
        await engine.dispose()


async def create_m2_case(session, *, suffix="001", route=RouteCode.M2.value):
    user = User(
        telegram_id=700_000 + int(suffix),
        telegram_username=f"m2_client_{suffix}",
        full_name="Клиент М2",
    )
    session.add(user)
    await session.flush()
    case = Case(
        case_number=f"TEST-M2-{suffix}",
        client_id=user.id,
        route=route,
        status=CaseStatus.M2_DESCRIPTION_PENDING.value,
        title="Консультация",
    )
    session.add(case)
    await session.flush()
    return user, case


async def create_consultation(session, case, user):
    return await ConsultationService(session).create_or_get_m2_consultation(
        case=case,
        actor_type="client",
        actor_id=user.id,
        source="telegram",
    )


async def advance_to_documents(session, case, user):
    service = ConsultationService(session)
    consultation = await create_consultation(session, case, user)
    await service.save_description(
        consultation=consultation,
        case=case,
        client_id=user.id,
        description="  Нужна консультация по срокам передачи квартиры.  ",
    )
    return service, consultation


async def advance_to_slot_selection(session, case, user, *, documents_uploaded=False):
    service, consultation = await advance_to_documents(session, case, user)
    await service.complete_documents_step(
        consultation=consultation,
        case=case,
        documents_uploaded=documents_uploaded,
        actor_id=user.id,
    )
    return service, consultation


async def add_available_slot(session, *, suffix=0):
    starts_at = datetime.now(timezone.utc) + timedelta(days=1, hours=suffix)
    slot = ConsultationSlot(
        lawyer_id=100 + suffix,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        status="available",
    )
    session.add(slot)
    await session.flush()
    return slot


async def history_count(session, case_id, action=None):
    query = select(func.count(AuditLog.id)).where(AuditLog.entity_id == case_id)
    if action:
        query = query.where(AuditLog.action == action)
    return (await session.execute(query)).scalar_one()


@pytest.mark.asyncio
async def test_creates_one_active_m2_consultation_and_syncs_case(consultation_db):
    async with consultation_db() as session:
        user, case = await create_m2_case(session)
        service = ConsultationService(session)

        first = await service.create_or_get_m2_consultation(
            case=case,
            actor_type="client",
            actor_id=user.id,
            source="telegram",
        )
        history_after_first = await history_count(session, case.id)
        second = await service.create_or_get_m2_consultation(
            case=case,
            actor_type="client",
            actor_id=user.id,
            source="telegram",
        )

        assert first.id == second.id
        assert first.status == ConsultationStatus.DESCRIPTION_PENDING.value
        assert case.route == RouteCode.M2.value
        assert case.status == CaseStatus.M2_DESCRIPTION_PENDING.value
        assert case.next_action == "Опишите вопрос для юриста"
        assert await history_count(session, case.id) == history_after_first == 1
        assert (
            await session.execute(
                select(func.count(Consultation.id)).where(Consultation.case_id == case.id)
            )
        ).scalar_one() == 1


@pytest.mark.asyncio
async def test_none_route_is_safely_initialized_as_m2(consultation_db):
    async with consultation_db() as session:
        user, case = await create_m2_case(session, route=None)

        await create_consultation(session, case, user)

        assert case.route == RouteCode.M2.value
        assert case.status == CaseStatus.M2_DESCRIPTION_PENDING.value


@pytest.mark.asyncio
async def test_m1_case_is_not_silently_changed_to_m2(consultation_db):
    async with consultation_db() as session:
        user, case = await create_m2_case(session, route=RouteCode.M1.value)

        with pytest.raises(ActiveConsultationConflictError, match="другого маршрута"):
            await create_consultation(session, case, user)

        assert case.route == RouteCode.M1.value
        assert await history_count(session, case.id) == 0


@pytest.mark.asyncio
async def test_multiple_active_consultations_are_reported_as_conflict(consultation_db):
    async with consultation_db() as session:
        user, case = await create_m2_case(session)
        session.add_all(
            [
                Consultation(case_id=case.id, status=ConsultationStatus.DESCRIPTION_PENDING.value),
                Consultation(case_id=case.id, status=ConsultationStatus.SLOT_PENDING.value),
            ]
        )
        await session.flush()

        with pytest.raises(ActiveConsultationConflictError, match="несколько активных"):
            await create_consultation(session, case, user)


@pytest.mark.parametrize("description", ["", "   ", "\n\t"])
@pytest.mark.asyncio
async def test_empty_description_is_rejected(consultation_db, description):
    async with consultation_db() as session:
        user, case = await create_m2_case(session)
        consultation = await create_consultation(session, case, user)
        initial_history = await history_count(session, case.id)

        with pytest.raises(ConsultationDescriptionError, match="не может быть пустым"):
            await ConsultationService(session).save_description(
                consultation=consultation,
                case=case,
                client_id=user.id,
                description=description,
            )

        assert consultation.status == ConsultationStatus.DESCRIPTION_PENDING.value
        assert consultation.client_description is None
        assert await history_count(session, case.id) == initial_history


@pytest.mark.asyncio
async def test_description_length_limit_is_enforced(consultation_db):
    async with consultation_db() as session:
        user, case = await create_m2_case(session)
        consultation = await create_consultation(session, case, user)

        with pytest.raises(ConsultationDescriptionError, match="4000"):
            await ConsultationService(session).save_description(
                consultation=consultation,
                case=case,
                client_id=user.id,
                description="x" * 4001,
            )


@pytest.mark.asyncio
async def test_description_is_stripped_saved_and_idempotent(consultation_db):
    async with consultation_db() as session:
        user, case = await create_m2_case(session)
        consultation = await create_consultation(session, case, user)
        service = ConsultationService(session)
        description = "Нужна консультация по условиям договора."

        result = await service.save_description(
            consultation=consultation,
            case=case,
            client_id=user.id,
            description=f"  {description}  ",
        )
        history_after_first = await history_count(
            session, case.id, "CONSULTATION_DESCRIPTION_SAVED"
        )
        repeated = await service.save_description(
            consultation=consultation,
            case=case,
            client_id=user.id,
            description=description,
        )

        assert result is repeated is consultation
        assert consultation.client_description == description
        assert consultation.status == ConsultationStatus.DOCUMENTS_OPTIONAL.value
        assert case.status == CaseStatus.M2_DOCUMENTS_OPTIONAL.value
        assert case.next_action == "Загрузите документы или пропустите этот шаг"
        assert history_after_first == 1
        assert await history_count(
            session, case.id, "CONSULTATION_DESCRIPTION_SAVED"
        ) == 1
        event = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "CONSULTATION_DESCRIPTION_SAVED"
                )
            )
        ).scalar_one()
        assert description not in str(event.new_value)
        assert event.new_value["description_length"] == len(description)


@pytest.mark.asyncio
async def test_different_description_after_step_is_rejected(consultation_db):
    async with consultation_db() as session:
        user, case = await create_m2_case(session)
        service, consultation = await advance_to_documents(session, case, user)
        original = consultation.client_description

        with pytest.raises(ConsultationDescriptionError, match="уже сохранено"):
            await service.save_description(
                consultation=consultation,
                case=case,
                client_id=user.id,
                description="Совершенно другой вопрос для юриста.",
            )

        assert consultation.client_description == original


@pytest.mark.parametrize(
    ("documents_uploaded", "expected_action"),
    [
        (False, "CONSULTATION_DOCUMENTS_SKIPPED"),
        (True, "CONSULTATION_DOCUMENTS_COMPLETED"),
    ],
)
@pytest.mark.asyncio
async def test_documents_step_moves_to_slot_pending_without_creating_documents(
    consultation_db, documents_uploaded, expected_action
):
    async with consultation_db() as session:
        user, case = await create_m2_case(session)
        service, consultation = await advance_to_documents(session, case, user)

        result = await service.complete_documents_step(
            consultation=consultation,
            case=case,
            documents_uploaded=documents_uploaded,
            actor_id=user.id,
        )
        history_after_first = await history_count(session, case.id, expected_action)
        repeated = await service.complete_documents_step(
            consultation=consultation,
            case=case,
            documents_uploaded=documents_uploaded,
            actor_id=user.id,
        )

        assert result is repeated is consultation
        assert consultation.status == ConsultationStatus.SLOT_PENDING.value
        assert case.status == CaseStatus.M2_SLOT_PENDING.value
        assert case.next_action == "Выберите удобное время консультации"
        assert history_after_first == 1
        assert await history_count(session, case.id, expected_action) == 1
        assert (
            await session.execute(
                select(func.count(Document.id)).where(Document.case_id == case.id)
            )
        ).scalar_one() == 0


@pytest.mark.asyncio
async def test_reserves_available_slot_and_is_idempotent(consultation_db):
    async with consultation_db() as session:
        user, case = await create_m2_case(session)
        service, consultation = await advance_to_slot_selection(session, case, user)
        slot = await add_available_slot(session)

        result, held = await service.reserve_pre_payment_slot(
            consultation=consultation,
            case=case,
            client_id=user.id,
            slot_id=slot.id,
        )
        history_after_first = await history_count(
            session, case.id, "CONSULTATION_SLOT_RESERVED"
        )
        repeated, repeated_slot = await service.reserve_pre_payment_slot(
            consultation=consultation,
            case=case,
            client_id=user.id,
            slot_id=slot.id,
        )

        assert result is repeated is consultation
        assert held.id == repeated_slot.id == slot.id
        assert held.status == "held"
        assert held.held_by_user_id == user.id
        assert held.consultation_id == consultation.id
        assert consultation.slot_id == slot.id
        assert consultation.status == ConsultationStatus.SLOT_RESERVED.value
        assert case.status == CaseStatus.M2_PAYMENT_PENDING.value
        assert case.next_action == "Перейдите к оплате консультации"
        assert history_after_first == 1
        assert await history_count(
            session, case.id, "CONSULTATION_SLOT_RESERVED"
        ) == 1


@pytest.mark.asyncio
async def test_missing_and_occupied_slots_leave_no_partial_changes(consultation_db):
    async with consultation_db() as session:
        user, case = await create_m2_case(session)
        service, consultation = await advance_to_slot_selection(session, case, user)
        occupied = await add_available_slot(session)
        occupied.status = "held"
        occupied.held_by_user_id = user.id + 1
        occupied.consultation_id = consultation.id + 100
        occupied.hold_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        await session.flush()
        initial_history = await history_count(session, case.id)

        for slot_id in (occupied.id, occupied.id + 1000):
            with pytest.raises(ConsultationSlotError):
                await service.reserve_pre_payment_slot(
                    consultation=consultation,
                    case=case,
                    client_id=user.id,
                    slot_id=slot_id,
                )

        assert consultation.slot_id is None
        assert consultation.status == ConsultationStatus.SLOT_PENDING.value
        assert case.status == CaseStatus.M2_SLOT_PENDING.value
        assert occupied.status == "held"
        assert occupied.held_by_user_id == user.id + 1
        assert await history_count(session, case.id) == initial_history


@pytest.mark.asyncio
async def test_reserved_consultation_cannot_silently_switch_slot(consultation_db):
    async with consultation_db() as session:
        user, case = await create_m2_case(session)
        service, consultation = await advance_to_slot_selection(session, case, user)
        first = await add_available_slot(session)
        second = await add_available_slot(session, suffix=1)
        await service.reserve_pre_payment_slot(
            consultation=consultation,
            case=case,
            client_id=user.id,
            slot_id=first.id,
        )

        with pytest.raises(ConsultationSlotError, match="другой слот"):
            await service.reserve_pre_payment_slot(
                consultation=consultation,
                case=case,
                client_id=user.id,
                slot_id=second.id,
            )

        assert consultation.slot_id == first.id
        assert first.status == "held"
        assert second.status == "available"


@pytest.mark.asyncio
async def test_invalid_reservation_transition_comes_from_state_machine(consultation_db):
    async with consultation_db() as session:
        user, case = await create_m2_case(session)
        consultation = await create_consultation(session, case, user)
        slot = await add_available_slot(session)

        with pytest.raises(InvalidConsultationTransition):
            await ConsultationService(session).reserve_pre_payment_slot(
                consultation=consultation,
                case=case,
                client_id=user.id,
                slot_id=slot.id,
            )

        assert slot.status == "available"
        assert consultation.slot_id is None


@pytest.mark.asyncio
async def test_moves_reserved_slot_to_payment_pending_idempotently(consultation_db):
    async with consultation_db() as session:
        user, case = await create_m2_case(session)
        service, consultation = await advance_to_slot_selection(session, case, user)
        slot = await add_available_slot(session)
        await service.reserve_pre_payment_slot(
            consultation=consultation,
            case=case,
            client_id=user.id,
            slot_id=slot.id,
        )

        result = await service.move_to_payment_pending(
            consultation=consultation,
            case=case,
            actor_id=user.id,
        )
        history_after_first = await history_count(
            session, case.id, "CONSULTATION_PAYMENT_PENDING"
        )
        repeated = await service.move_to_payment_pending(
            consultation=consultation,
            case=case,
            actor_id=user.id,
        )

        assert result is repeated is consultation
        assert consultation.status == ConsultationStatus.PAYMENT_PENDING.value
        assert case.status == CaseStatus.M2_PAYMENT_PENDING.value
        assert case.next_action == "Оплатите консультацию для подтверждения записи"
        assert slot.status == "held"
        assert history_after_first == 1
        assert await history_count(
            session, case.id, "CONSULTATION_PAYMENT_PENDING"
        ) == 1


@pytest.mark.asyncio
async def test_payment_pending_requires_a_real_current_hold(consultation_db):
    async with consultation_db() as session:
        user, case = await create_m2_case(session)
        service, consultation = await advance_to_slot_selection(session, case, user)
        consultation.status = ConsultationStatus.SLOT_RESERVED.value

        with pytest.raises(ConsultationSlotError, match="требуется удерживаемый слот"):
            await service.move_to_payment_pending(
                consultation=consultation,
                case=case,
                actor_id=user.id,
            )

        assert consultation.status == ConsultationStatus.SLOT_RESERVED.value


@pytest.mark.asyncio
async def test_released_slot_blocks_payment_pending_without_history(consultation_db):
    async with consultation_db() as session:
        user, case = await create_m2_case(session)
        service, consultation = await advance_to_slot_selection(session, case, user)
        slot = await add_available_slot(session)
        await service.reserve_pre_payment_slot(
            consultation=consultation,
            case=case,
            client_id=user.id,
            slot_id=slot.id,
        )
        await service.slots.release_slot(slot.id, consultation.id)
        initial_history = await history_count(session, case.id)

        with pytest.raises(ConsultationSlotError, match="отсутствует"):
            await service.move_to_payment_pending(
                consultation=consultation,
                case=case,
                actor_id=user.id,
            )

        assert consultation.status == ConsultationStatus.SLOT_RESERVED.value
        assert await history_count(session, case.id) == initial_history


@pytest.mark.asyncio
async def test_legacy_entry_points_keep_current_handler_contract(consultation_db):
    async with consultation_db() as session:
        user, case = await create_m2_case(
            session,
            suffix="002",
            route=RouteCode.M1.value,
        )
        original_case_state = (case.route, case.status, case.next_action)
        service = ConsultationService(session)

        consultation = await service.get_or_create_for_case(case)
        repeated = await service.get_or_create_for_case(case)
        slot = await add_available_slot(session)
        reserved, held = await service.reserve_slot(
            consultation=consultation,
            case=case,
            client_id=user.id,
            slot_id=slot.id,
        )
        consultation.status = ConsultationStatus.BOOKED.value
        described = await service.save_description(
            consultation=consultation,
            case=case,
            client_id=user.id,
            description="  Вопрос, сохранённый текущим Telegram-flow после оплаты.  ",
        )

        assert consultation is repeated is reserved is described
        assert held.id == slot.id
        assert consultation.status == ConsultationStatus.BOOKED.value
        assert consultation.client_description == (
            "Вопрос, сохранённый текущим Telegram-flow после оплаты."
        )
        assert (case.route, case.status, case.next_action) == original_case_state
        assert await history_count(
            session, case.id, "CONSULTATION_DESCRIPTION_SAVED"
        ) == 1


@pytest.mark.asyncio
async def test_past_slot_is_rejected_before_hold(consultation_db):
    async with consultation_db() as session:
        user, case = await create_m2_case(session)
        service, consultation = await advance_to_slot_selection(session, case, user)
        starts_at = datetime.now(timezone.utc) - timedelta(hours=2)
        slot = ConsultationSlot(
            lawyer_id=404,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            status="available",
        )
        session.add(slot)
        await session.flush()
        initial_history = await history_count(session, case.id)

        with pytest.raises(ConsultationSlotError, match="недоступен"):
            await service.reserve_pre_payment_slot(
                consultation=consultation,
                case=case,
                client_id=user.id,
                slot_id=slot.id,
            )

        assert slot.status == "available"
        assert consultation.slot_id is None
        assert consultation.status == ConsultationStatus.SLOT_PENDING.value
        assert case.status == CaseStatus.M2_SLOT_PENDING.value
        assert await history_count(session, case.id) == initial_history


@pytest.mark.asyncio
async def test_post_hold_failure_rolls_back_savepoint_changes(
    consultation_db, monkeypatch
):
    async with consultation_db() as session:
        user, case = await create_m2_case(session)
        service, consultation = await advance_to_slot_selection(session, case, user)
        slot = await add_available_slot(session)
        slot_id = slot.id
        consultation_id = consultation.id
        case_id = case.id
        initial_history = await history_count(session, case.id)
        original_hold_slot = service.slots.hold_slot

        async def hold_then_fail(slot_id, user_id, consultation_id):
            await original_hold_slot(slot_id, user_id, consultation_id)
            raise SlotUnavailableError("simulated concurrent hold failure")

        monkeypatch.setattr(service.slots, "hold_slot", hold_then_fail)

        with pytest.raises(ConsultationSlotError, match="стал недоступен"):
            await service.reserve_pre_payment_slot(
                consultation=consultation,
                case=case,
                client_id=user.id,
                slot_id=slot.id,
            )

        await session.refresh(consultation)
        await session.refresh(case)
        refreshed_slot = await session.get(ConsultationSlot, slot_id)
        assert refreshed_slot.status == "available"
        assert refreshed_slot.held_by_user_id is None
        assert refreshed_slot.consultation_id is None
        assert consultation.slot_id is None
        assert consultation.id == consultation_id
        assert consultation.status == ConsultationStatus.SLOT_PENDING.value
        assert case.id == case_id
        assert case.status == CaseStatus.M2_SLOT_PENDING.value
        assert await history_count(session, case.id) == initial_history
