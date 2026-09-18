from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cases.case_service import CaseService
from app.domain.cases.case_transition_policy import CaseTransitionError
from app.domain.consultations.outcome_service import ConsultationOutcomeService
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.case_transition import (
    CaseTransitionCommand,
    CaseTransitionOutboxEvent,
)
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.user import User


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _count(db, model, *criteria) -> int:
    statement = select(func.count()).select_from(model)
    if criteria:
        statement = statement.where(*criteria)
    return int((await db.execute(statement)).scalar_one())


def test_m2_to_m1_handoff_is_lawyer_only_and_never_dual_route() -> None:
    async def scenario() -> None:
        engine, sessions = await _database()

        async with sessions() as db:
            user = User(telegram_id=980018101, full_name="PM018 M2 client")
            lawyer = Lawyer(full_name="PM018 Lawyer", is_active=True)
            db.add_all([user, lawyer])
            await db.flush()
            case = Case(
                case_number="PM018-M2-M1-AUTHORITY",
                client_id=user.id,
                route="M2",
                status=CaseStatus.M2_CONSULTATION_DONE,
            )
            db.add(case)
            await db.flush()
            user_id = int(user.id)
            lawyer_id = int(lawyer.id)
            case_id = int(case.id)
            await db.commit()

        async with sessions() as db:
            case = await db.get(Case, case_id)
            assert case is not None
            with pytest.raises(CaseTransitionError):
                await CaseService(db).transfer_to_m1(
                    case=case,
                    actor_type="client",
                    actor_id=user_id,
                    expected_version=1,
                    idempotency_key="pm018:m2-to-m1:client-denied",
                )
            await db.rollback()

        async with sessions() as db:
            case = await db.get(Case, case_id)
            assert case is not None
            moved = await CaseService(db).transfer_to_m1(
                case=case,
                actor_type="lawyer",
                actor_id=lawyer_id,
                expected_version=1,
                idempotency_key="pm018:m2-to-m1:lawyer",
                correlation_id="pm018:m2-to-m1",
            )
            assert moved.status == CaseStatus.M1_DOCUMENTS_PENDING
            assert str(moved.route) == "M1"
            assert int(moved.version) == 2
            await db.commit()

        async with sessions() as db:
            case = await db.get(Case, case_id)
            assert case is not None
            assert case.status == CaseStatus.M1_DOCUMENTS_PENDING
            assert str(case.route) == "M1"
            assert not str(case.status).startswith("M2_")
            assert await _count(
                db,
                CaseTransitionCommand,
                CaseTransitionCommand.case_id == case_id,
                CaseTransitionCommand.action == "CASE_TRANSFERRED_TO_M1",
            ) == 1
            assert await _count(
                db,
                CaseTransitionOutboxEvent,
                CaseTransitionOutboxEvent.case_id == case_id,
            ) == 1

        await engine.dispose()

    asyncio.run(scenario())


def test_real_consultation_outcome_to_m1_is_recoverable_and_idempotent() -> None:
    async def scenario() -> None:
        engine, sessions = await _database()
        now = datetime.now(timezone.utc)

        async with sessions() as db:
            user = User(telegram_id=980018102, full_name="PM018 Outcome Client")
            lawyer = Lawyer(
                full_name="PM018 Outcome Lawyer",
                is_active=True,
                telegram_id=980018202,
            )
            db.add_all([user, lawyer])
            await db.flush()

            case = Case(
                case_number="PM018-M2-M1-OUTCOME",
                client_id=user.id,
                route="M2",
                status=CaseStatus.M2_CONSULTATION_BOOKED,
            )
            db.add(case)
            await db.flush()

            slot = ConsultationSlot(
                lawyer_id=lawyer.id,
                starts_at=now - timedelta(hours=1),
                ends_at=now + timedelta(hours=1),
                status="booked",
                held_by_user_id=user.id,
            )
            db.add(slot)
            await db.flush()

            consultation = Consultation(
                case_id=case.id,
                lawyer_id=lawyer.id,
                slot_id=slot.id,
                status=ConsultationStatus.BOOKED,
                consultation_type="online",
                subject_type="new_or_other",
                scheduled_at=slot.starts_at,
                client_description=(
                    "Подробное описание ситуации клиента для проверки PM-018."
                ),
            )
            db.add(consultation)
            await db.flush()
            slot.consultation_id = consultation.id
            await db.flush()

            lawyer_id = int(lawyer.id)
            case_id = int(case.id)
            consultation_id = int(consultation.id)
            await db.commit()

        result_text = (
            "По итогам консультации юрист подтвердил переход к сопровождению M1."
        )

        # Production M2 outcome path performs the authority handoff.
        async with sessions() as db:
            completed = await ConsultationOutcomeService(db).complete(
                consultation_id=consultation_id,
                lawyer_id=lawyer_id,
                result=result_text,
                decision="to_m1",
            )
            assert completed.status == ConsultationStatus.DONE
            await db.commit()

        # Simulate commit-before-response retry of the whole consultation action.
        async with sessions() as db:
            replay = await ConsultationOutcomeService(db).complete(
                consultation_id=consultation_id,
                lawyer_id=lawyer_id,
                result=result_text,
                decision="to_m1",
            )
            assert replay.status == ConsultationStatus.DONE
            await db.commit()

        async with sessions() as db:
            case = await db.get(Case, case_id)
            consultation = await db.get(Consultation, consultation_id)
            assert case is not None
            assert consultation is not None
            assert case.status == CaseStatus.M1_DOCUMENTS_PENDING
            assert str(case.route) == "M1"
            assert int(case.version) == 2
            assert consultation.status == ConsultationStatus.DONE
            assert consultation.decision == "to_m1"

            commands = list(
                (
                    await db.execute(
                        select(CaseTransitionCommand).where(
                            CaseTransitionCommand.case_id == case_id
                        )
                    )
                ).scalars().all()
            )
            assert len(commands) == 1
            command = commands[0]
            assert command.action == "CASE_TRANSFERRED_TO_M1"
            assert command.actor_type == "lawyer"
            assert command.actor_id == lawyer_id
            assert (
                command.idempotency_key
                == f"consultation:{consultation_id}:complete:to_m1"
            )
            assert command.source_status == CaseStatus.M2_CONSULTATION_BOOKED
            assert command.target_status == CaseStatus.M1_DOCUMENTS_PENDING
            assert int(command.applied_version) == 2

            assert await _count(
                db,
                CaseTransitionOutboxEvent,
                CaseTransitionOutboxEvent.case_id == case_id,
            ) == 1
            assert await _count(
                db,
                AuditLog,
                AuditLog.entity_type == "case",
                AuditLog.entity_id == case_id,
                AuditLog.action == "CASE_TRANSFERRED_TO_M1",
            ) == 1
            assert await _count(
                db,
                AuditLog,
                AuditLog.entity_type == "case",
                AuditLog.entity_id == case_id,
                AuditLog.action == "CONSULTATION_COMPLETED",
            ) == 1

        await engine.dispose()

    asyncio.run(scenario())
