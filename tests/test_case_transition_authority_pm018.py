from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cases.case_service import CaseService
from app.domain.statuses.case_statuses import CaseStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.case_transition import (
    CaseTransitionCommand,
    CaseTransitionOutboxEvent,
)
from app.models.user import User


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_case(
    session_factory,
    *,
    telegram_id: int,
    case_number: str,
    status: str = CaseStatus.NEW.value,
    route: str | None = None,
) -> tuple[int, int]:
    async with session_factory() as db:
        user = User(telegram_id=telegram_id, full_name=case_number)
        db.add(user)
        await db.flush()
        case = Case(
            case_number=case_number,
            client_id=user.id,
            status=status,
            route=route,
        )
        db.add(case)
        await db.flush()
        user_id, case_id = int(user.id), int(case.id)
        await db.commit()
    return user_id, case_id


async def _count(db, model, *criteria) -> int:
    statement = select(func.count()).select_from(model)
    if criteria:
        statement = statement.where(*criteria)
    return int((await db.execute(statement)).scalar_one())


def test_applied_transition_increments_version_and_writes_atomic_evidence() -> None:
    async def scenario() -> None:
        engine, sessions = await _database()
        user_id, case_id = await _seed_case(
            sessions,
            telegram_id=980018001,
            case_number="PM018-APPLIED",
        )

        async with sessions() as db:
            case = await db.get(Case, case_id)
            assert case is not None
            assert int(case.version) == 1

            result = await CaseService(db).execute_transition(
                case=case,
                next_status=CaseStatus.CALCULATOR_STARTED,
                actor_type="client",
                actor_id=user_id,
                expected_version=1,
                idempotency_key="callback:pm018:applied",
                correlation_id="telegram:update:applied",
            )

            assert result.outcome == "APPLIED"
            assert result.changed is True
            assert result.applied_version == 2
            assert result.case.status == CaseStatus.CALCULATOR_STARTED
            assert int(result.case.version) == 2
            assert await _count(
                db,
                CaseTransitionCommand,
                CaseTransitionCommand.case_id == case_id,
            ) == 1
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
                AuditLog.action == "CASE_STATUS_CHANGED",
            ) == 1
            await db.commit()

        await engine.dispose()

    asyncio.run(scenario())


def test_stale_expected_version_is_side_effect_free() -> None:
    async def scenario() -> None:
        engine, sessions = await _database()
        user_id, case_id = await _seed_case(
            sessions,
            telegram_id=980018002,
            case_number="PM018-STALE",
        )

        async with sessions() as db:
            case = await db.get(Case, case_id)
            assert case is not None
            result = await CaseService(db).execute_transition(
                case=case,
                next_status=CaseStatus.CALCULATOR_STARTED,
                actor_type="client",
                actor_id=user_id,
                expected_version=99,
                idempotency_key="callback:pm018:stale",
            )

            assert result.outcome == "STALE"
            assert result.changed is False
            assert result.applied_version == 1
            assert result.case.status == CaseStatus.NEW
            assert int(result.case.version) == 1
            assert await _count(
                db,
                CaseTransitionCommand,
                CaseTransitionCommand.case_id == case_id,
            ) == 0
            assert await _count(
                db,
                CaseTransitionOutboxEvent,
                CaseTransitionOutboxEvent.case_id == case_id,
            ) == 0
            assert await _count(
                db,
                AuditLog,
                AuditLog.entity_type == "case",
                AuditLog.entity_id == case_id,
            ) == 0

        await engine.dispose()

    asyncio.run(scenario())


def test_commit_before_response_retry_replays_before_stale_check() -> None:
    async def scenario() -> None:
        engine, sessions = await _database()
        user_id, case_id = await _seed_case(
            sessions,
            telegram_id=980018003,
            case_number="PM018-REPLAY",
        )
        key = "callback:pm018:commit-before-response"

        # First request commits, but the caller is assumed to lose the response.
        async with sessions() as db:
            case = await db.get(Case, case_id)
            assert case is not None
            first = await CaseService(db).execute_transition(
                case=case,
                next_status=CaseStatus.CALCULATOR_STARTED,
                actor_type="client",
                actor_id=user_id,
                expected_version=1,
                idempotency_key=key,
            )
            assert first.outcome == "APPLIED"
            command_id = first.command_id
            await db.commit()

        # Telegram/network retry still carries version 1. The durable command
        # identity must win before optimistic-stale validation.
        async with sessions() as db:
            case = await db.get(Case, case_id)
            assert case is not None
            assert int(case.version) == 2
            replay = await CaseService(db).execute_transition(
                case=case,
                next_status=CaseStatus.CALCULATOR_STARTED,
                actor_type="client",
                actor_id=user_id,
                expected_version=1,
                idempotency_key=key,
            )
            assert replay.outcome == "REPLAYED"
            assert replay.changed is False
            assert replay.command_id == command_id
            assert replay.applied_version == 2

            stale_other_command = await CaseService(db).execute_transition(
                case=case,
                next_status=CaseStatus.CALCULATED,
                actor_type="client",
                actor_id=user_id,
                expected_version=1,
                idempotency_key="callback:pm018:different-command",
            )
            assert stale_other_command.outcome == "STALE"

            assert await _count(
                db,
                CaseTransitionCommand,
                CaseTransitionCommand.case_id == case_id,
            ) == 1
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
                AuditLog.action == "CASE_STATUS_CHANGED",
            ) == 1

        await engine.dispose()

    asyncio.run(scenario())


def test_foreign_client_isolation_and_transaction_rollback_leave_no_partial_evidence() -> None:
    async def scenario() -> None:
        engine, sessions = await _database()
        owner_id, case_id = await _seed_case(
            sessions,
            telegram_id=980018004,
            case_number="PM018-ISOLATION",
        )
        foreign_id, _ = await _seed_case(
            sessions,
            telegram_id=980018005,
            case_number="PM018-FOREIGN",
        )

        async with sessions() as db:
            case = await db.get(Case, case_id)
            assert case is not None
            with pytest.raises(LookupError):
                await CaseService(db).execute_transition(
                    case=case,
                    next_status=CaseStatus.CALCULATOR_STARTED,
                    actor_type="client",
                    actor_id=foreign_id,
                    request_client_id=foreign_id,
                    expected_version=1,
                    idempotency_key="callback:pm018:foreign",
                )
            await db.rollback()

        # Inject failure after every authority row has been flushed but before
        # commit. Rollback must restore Case and remove journal/audit/outbox.
        async with sessions() as db:
            case = await db.get(Case, case_id)
            assert case is not None
            result = await CaseService(db).execute_transition(
                case=case,
                next_status=CaseStatus.CALCULATOR_STARTED,
                actor_type="client",
                actor_id=owner_id,
                expected_version=1,
                idempotency_key="callback:pm018:rollback",
            )
            assert result.outcome == "APPLIED"
            await db.rollback()

        async with sessions() as db:
            case = await db.get(Case, case_id)
            assert case is not None
            assert case.status == CaseStatus.NEW
            assert int(case.version) == 1
            assert await _count(
                db,
                CaseTransitionCommand,
                CaseTransitionCommand.case_id == case_id,
            ) == 0
            assert await _count(
                db,
                CaseTransitionOutboxEvent,
                CaseTransitionOutboxEvent.case_id == case_id,
            ) == 0
            assert await _count(
                db,
                AuditLog,
                AuditLog.entity_type == "case",
                AuditLog.entity_id == case_id,
            ) == 0

        await engine.dispose()

    asyncio.run(scenario())
