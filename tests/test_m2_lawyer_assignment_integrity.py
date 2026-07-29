from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.consultations.payment_lifecycle_service import (
    ConsultationPaymentLifecycleError,
    ConsultationPaymentLifecycleService,
)
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.notification import Notification
from app.models.user import User


@pytest.fixture
async def assignment_lifecycle_db(tmp_path):
    database_path = tmp_path / "m2-lawyer-assignment-integrity.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def seed_paid_consultation(
    session,
    *,
    suffix: int,
    workload_limit: int,
):
    starts_at = datetime.now(timezone.utc) + timedelta(days=2)
    user = User(
        telegram_id=995_000 + suffix,
        telegram_username=f"assignment_integrity_{suffix}",
        full_name=f"Клиент {suffix}",
    )
    lawyer = Lawyer(
        full_name=f"Юрист {suffix}",
        specialization="Споры по ДДУ",
        workload_limit=workload_limit,
        is_active=True,
    )
    session.add_all([user, lawyer])
    await session.flush()

    case = Case(
        case_number=f"M2-ASSIGNMENT-{suffix}",
        client_id=user.id,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_PAYMENT_PENDING.value,
        title="Оплаченная консультация",
        next_action="Ожидайте подтверждения консультации",
    )
    session.add(case)
    await session.flush()

    consultation = Consultation(
        case_id=case.id,
        lawyer_id=lawyer.id,
        status=ConsultationStatus.PAID_PENDING_CONFIRMATION.value,
        scheduled_at=starts_at,
        consultation_type="online",
    )
    session.add(consultation)
    await session.flush()

    slot = ConsultationSlot(
        lawyer_id=lawyer.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=45),
        status="booked",
        held_by_user_id=user.id,
        consultation_id=consultation.id,
    )
    session.add(slot)
    await session.flush()
    consultation.slot_id = slot.id
    await session.flush()
    return user, lawyer, case, consultation, slot


async def audit_count(session, *, case_id: int, action: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id == case_id,
                    AuditLog.action == action,
                )
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_lawyer_confirmation_uses_canonical_assignment_once(
    assignment_lifecycle_db,
):
    async with assignment_lifecycle_db() as session:
        _, lawyer, case, consultation, _ = await seed_paid_consultation(
            session,
            suffix=1,
            workload_limit=2,
        )
        lifecycle = ConsultationPaymentLifecycleService(session)

        booked = await lifecycle.confirm_by_lawyer(
            case=case,
            lawyer_id=lawyer.id,
            source="test",
        )
        await session.commit()

        assert booked.status == ConsultationStatus.BOOKED.value
        assert case.assigned_lawyer_id == lawyer.id
        assert case.status == CaseStatus.M2_CONSULTATION_BOOKED.value
        assert await audit_count(
            session,
            case_id=case.id,
            action="LAWYER_ASSIGNED",
        ) == 1
        assert await audit_count(
            session,
            case_id=case.id,
            action="CONSULTATION_BOOKED_AFTER_LAWYER_CONFIRMATION",
        ) == 1

        repeated = await lifecycle.confirm_by_lawyer(
            case=case,
            lawyer_id=lawyer.id,
            source="test-retry",
        )
        await session.commit()

        assert repeated.status == ConsultationStatus.BOOKED.value
        assert await audit_count(
            session,
            case_id=case.id,
            action="LAWYER_ASSIGNED",
        ) == 1
        assert await audit_count(
            session,
            case_id=case.id,
            action="CONSULTATION_BOOKED_AFTER_LAWYER_CONFIRMATION",
        ) == 1


@pytest.mark.asyncio
async def test_capacity_conflict_leaves_paid_consultation_unbooked(
    assignment_lifecycle_db,
):
    async with assignment_lifecycle_db() as session:
        user, lawyer, case, consultation, slot = await seed_paid_consultation(
            session,
            suffix=2,
            workload_limit=1,
        )
        occupied = Case(
            case_number="CAPACITY-OCCUPIED",
            client_id=user.id,
            route=RouteCode.M1.value,
            status=CaseStatus.NEW.value,
            title="Другое активное дело",
            assigned_lawyer_id=lawyer.id,
        )
        session.add(occupied)
        await session.flush()

        with pytest.raises(
            ConsultationPaymentLifecycleError,
            match="достигнут лимит активных дел",
        ):
            await ConsultationPaymentLifecycleService(session).confirm_by_lawyer(
                case=case,
                lawyer_id=lawyer.id,
                source="test",
            )

        await session.refresh(case)
        await session.refresh(consultation)
        await session.refresh(slot)
        assert consultation.status == ConsultationStatus.PAID_PENDING_CONFIRMATION.value
        assert consultation.lawyer_id == lawyer.id
        assert case.assigned_lawyer_id is None
        assert case.status == CaseStatus.M2_PAYMENT_PENDING.value
        assert slot.status == "booked"
        assert await audit_count(
            session,
            case_id=case.id,
            action="LAWYER_ASSIGNED",
        ) == 0
        assert await audit_count(
            session,
            case_id=case.id,
            action="CONSULTATION_CONFIRMED_BY_LAWYER",
        ) == 0
        assert await audit_count(
            session,
            case_id=case.id,
            action="CONSULTATION_BOOKED_AFTER_LAWYER_CONFIRMATION",
        ) == 0
        notifications = int(
            (
                await session.execute(
                    select(func.count(Notification.id)).where(
                        Notification.case_id == case.id,
                        Notification.event_code == "M2_CONSULTATION_CONFIRMED",
                    )
                )
            ).scalar_one()
        )
        assert notifications == 0
