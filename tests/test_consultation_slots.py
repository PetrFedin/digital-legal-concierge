from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.slot_service import SlotService, SlotUnavailableError
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.user import User


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
        first_slot = await SlotService(first).hold_slot(
            slot_id,
            user_id=101,
            consultation_id=201,
        )
        await first.commit()
        assert first_slot.status == "held"

        with pytest.raises(SlotUnavailableError):
            await SlotService(second).hold_slot(
                slot_id,
                user_id=102,
                consultation_id=202,
            )
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


@pytest.mark.asyncio
async def test_expired_m2_hold_resets_consultation_case_and_can_be_reused(tmp_path):
    database_path = tmp_path / "expired-m2.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    starts_at = datetime.now(timezone.utc) + timedelta(days=1)
    async with session_factory() as session:
        first_user = User(
            telegram_id=900_001,
            telegram_username="expired_hold_client",
            full_name="Клиент с истёкшим резервом",
        )
        session.add(first_user)
        await session.flush()

        first_case = Case(
            case_number="TEST-M2-EXPIRED-001",
            client_id=first_user.id,
            route=RouteCode.M2.value,
            status=CaseStatus.M2_PAYMENT_PENDING.value,
            title="Консультация",
            next_action="Перейдите к оплате консультации",
        )
        session.add(first_case)
        await session.flush()

        first_consultation = Consultation(
            case_id=first_case.id,
            lawyer_id=1,
            status=ConsultationStatus.SLOT_RESERVED.value,
            scheduled_at=starts_at,
        )
        session.add(first_consultation)
        await session.flush()

        slot = ConsultationSlot(
            lawyer_id=1,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            status="held",
            held_by_user_id=first_user.id,
            consultation_id=first_consultation.id,
            hold_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        session.add(slot)
        await session.flush()
        first_consultation.slot_id = slot.id
        await session.commit()

        slot_id = slot.id
        first_case_id = first_case.id
        first_consultation_id = first_consultation.id

    async with session_factory() as session:
        available = await SlotService(session).get_available_slots()
        await session.commit()

        slot = await session.get(ConsultationSlot, slot_id)
        consultation = await session.get(Consultation, first_consultation_id)
        case = await session.get(Case, first_case_id)
        audit_events = list(
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == first_case_id,
                        AuditLog.action == "CONSULTATION_SLOT_HOLD_EXPIRED",
                    )
                )
            )
            .scalars()
            .all()
        )

        assert any(item.id == slot_id for item in available)
        assert slot is not None
        assert slot.status == "available"
        assert slot.hold_expires_at is None
        assert slot.held_by_user_id is None
        assert slot.consultation_id is None

        assert consultation is not None
        assert consultation.slot_id is None
        assert consultation.lawyer_id is None
        assert consultation.scheduled_at is None
        assert consultation.status == ConsultationStatus.SLOT_PENDING.value

        assert case is not None
        assert case.route == RouteCode.M2.value
        assert case.status == CaseStatus.M2_SLOT_PENDING.value
        assert case.next_action == "Выберите удобное время консультации"

        assert len(audit_events) == 1
        assert audit_events[0].old_value["slot_id"] == slot_id
        assert audit_events[0].new_value["slot_id"] is None

    async with session_factory() as session:
        second_user = User(
            telegram_id=900_002,
            telegram_username="next_hold_client",
            full_name="Следующий клиент",
        )
        session.add(second_user)
        await session.flush()

        second_case = Case(
            case_number="TEST-M2-EXPIRED-002",
            client_id=second_user.id,
            route=RouteCode.M2.value,
            status=CaseStatus.M2_SLOT_PENDING.value,
            title="Консультация",
            next_action="Выберите удобное время консультации",
        )
        session.add(second_case)
        await session.flush()

        second_consultation = Consultation(
            case_id=second_case.id,
            status=ConsultationStatus.SLOT_PENDING.value,
        )
        session.add(second_consultation)
        await session.flush()

        reused = await SlotService(session).hold_slot(
            slot_id,
            user_id=second_user.id,
            consultation_id=second_consultation.id,
        )
        await session.commit()

        assert reused.status == "held"
        assert reused.held_by_user_id == second_user.id
        assert reused.consultation_id == second_consultation.id

    await engine.dispose()
