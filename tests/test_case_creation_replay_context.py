from __future__ import annotations

import asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cases.case_service import CaseService
from app.domain.statuses.case_statuses import CaseStatus
from app.models import Base
from app.models.case import Case
from app.models.case_creation_request import CaseCreationRequest
from app.models.client_case_context import ClientCaseContext
from app.models.user import User


def test_active_case_creation_replay_does_not_reselect_older_case() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as db:
            user = User(
                telegram_id=990000401,
                full_name="Active replay context",
            )
            db.add(user)
            await db.flush()
            service = CaseService(db)

            older = await service.create_case_for_operation(
                client=user,
                operation_key="test:active-replay:older",
                purpose="calculator_start",
                status=CaseStatus.CALCULATOR_STARTED,
                title="Older active matter",
            )
            current = await service.create_case_for_operation(
                client=user,
                operation_key="test:active-replay:current",
                purpose="calculator_start",
                status=CaseStatus.CALCULATOR_STARTED,
                title="Current selected matter",
            )
            older_id = int(older.id)
            current_id = int(current.id)
            await db.commit()

            replayed = await service.create_case_for_operation(
                client=user,
                operation_key="test:active-replay:older",
                purpose="calculator_start",
                status=CaseStatus.CALCULATOR_STARTED,
                title="Ignored replay arguments",
            )
            assert int(replayed.id) == older_id

            context = await db.scalar(
                select(ClientCaseContext).where(
                    ClientCaseContext.client_id == int(user.id)
                )
            )
            assert context is not None
            assert int(context.selected_case_id) == current_id

            case_count = await db.scalar(
                select(func.count(Case.id)).where(Case.client_id == int(user.id))
            )
            operation_count = await db.scalar(
                select(func.count(CaseCreationRequest.id)).where(
                    CaseCreationRequest.client_id == int(user.id)
                )
            )
            assert case_count == 2
            assert operation_count == 2

        await engine.dispose()

    asyncio.run(scenario())


def test_terminal_case_creation_replay_does_not_reselect_completed_case() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as db:
            user = User(
                telegram_id=990000402,
                full_name="Terminal replay context",
            )
            db.add(user)
            await db.flush()
            service = CaseService(db)

            completed = await service.create_case_for_operation(
                client=user,
                operation_key="test:terminal-replay:completed",
                purpose="calculator_start",
                route="M1",
                status=CaseStatus.M1_SUCCESS_FEE_RECEIVED,
                title="Completed matter",
            )
            current = await service.create_case_for_operation(
                client=user,
                operation_key="test:terminal-replay:current",
                purpose="calculator_start",
                status=CaseStatus.CALCULATOR_STARTED,
                title="Current selected matter",
            )
            completed_id = int(completed.id)
            current_id = int(current.id)

            await service.change_status(
                case=completed,
                next_status=CaseStatus.M1_CLOSED,
                actor_type="system",
                actor_id=None,
                comment="Complete fixture before delayed replay",
            )
            await db.commit()

            replayed = await service.create_case_for_operation(
                client=user,
                operation_key="test:terminal-replay:completed",
                purpose="calculator_start",
                status=CaseStatus.CALCULATOR_STARTED,
                title="Ignored replay arguments",
            )
            assert int(replayed.id) == completed_id
            assert str(replayed.status) == CaseStatus.M1_CLOSED.value

            context = await db.scalar(
                select(ClientCaseContext).where(
                    ClientCaseContext.client_id == int(user.id)
                )
            )
            assert context is not None
            assert int(context.selected_case_id) == current_id

        await engine.dispose()

    asyncio.run(scenario())
