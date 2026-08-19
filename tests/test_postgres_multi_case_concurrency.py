from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.domain.cases.case_service import CaseService
from app.domain.statuses.case_statuses import CaseStatus
from app.models.calculation import Calculation
from app.models.case import Case
from app.models.case_creation_request import CaseCreationRequest
from app.models.client_case_context import ClientCaseContext
from app.models.user import User


_DATABASE_URL = str(settings.database_url)
pytestmark = pytest.mark.skipif(
    not _DATABASE_URL.startswith(("postgresql", "postgres")),
    reason="PostgreSQL row-lock concurrency contract",
)


def _telegram_id() -> int:
    return 8_000_000_000_000 + (uuid.uuid4().int % 1_000_000_000)


async def _new_client(label: str) -> int:
    async with AsyncSessionLocal() as db:
        user = User(telegram_id=_telegram_id(), full_name=label)
        db.add(user)
        await db.commit()
        return int(user.id)


async def _create_for_operation(
    *,
    user_id: int,
    operation_key: str,
    purpose: str,
) -> int:
    async with AsyncSessionLocal() as db:
        client = await db.get(User, int(user_id))
        assert client is not None
        case = await CaseService(db).create_case_for_operation(
            client=client,
            operation_key=operation_key,
            purpose=purpose,
            status=CaseStatus.CALCULATOR_STARTED,
            title="Concurrency probe",
        )
        case_id = int(case.id)
        await db.commit()
        return case_id


async def _scenario_same_operation_is_exactly_once() -> None:
    user_id = await _new_client("Concurrent same operation")
    operation_key = f"pytest:same:{uuid.uuid4().hex}"

    first, second = await asyncio.gather(
        _create_for_operation(
            user_id=user_id,
            operation_key=operation_key,
            purpose="calculator_start",
        ),
        _create_for_operation(
            user_id=user_id,
            operation_key=operation_key,
            purpose="calculator_start",
        ),
    )

    assert first == second
    async with AsyncSessionLocal() as db:
        case_count = await db.scalar(
            select(func.count(Case.id)).where(Case.client_id == user_id)
        )
        request_count = await db.scalar(
            select(func.count(CaseCreationRequest.id)).where(
                CaseCreationRequest.client_id == user_id,
                CaseCreationRequest.operation_key == operation_key,
            )
        )
        context = await db.scalar(
            select(ClientCaseContext).where(ClientCaseContext.client_id == user_id)
        )
        assert case_count == 1
        assert request_count == 1
        assert context is not None
        assert int(context.selected_case_id) == first


async def _scenario_distinct_operations_create_distinct_active_cases() -> None:
    user_id = await _new_client("Concurrent distinct operations")
    key_a = f"pytest:distinct:a:{uuid.uuid4().hex}"
    key_b = f"pytest:distinct:b:{uuid.uuid4().hex}"

    case_a, case_b = await asyncio.gather(
        _create_for_operation(
            user_id=user_id,
            operation_key=key_a,
            purpose="calculator_start",
        ),
        _create_for_operation(
            user_id=user_id,
            operation_key=key_b,
            purpose="calculator_start",
        ),
    )

    assert case_a != case_b
    async with AsyncSessionLocal() as db:
        active_cases = list(
            (
                await db.execute(
                    select(Case)
                    .where(Case.client_id == user_id)
                    .order_by(Case.id.asc())
                )
            ).scalars().all()
        )
        requests = list(
            (
                await db.execute(
                    select(CaseCreationRequest).where(
                        CaseCreationRequest.client_id == user_id
                    )
                )
            ).scalars().all()
        )
        context = await db.scalar(
            select(ClientCaseContext).where(ClientCaseContext.client_id == user_id)
        )
        assert {int(item.id) for item in active_cases} == {case_a, case_b}
        assert len(requests) == 2
        assert context is not None
        assert int(context.selected_case_id) in {case_a, case_b}

        # Calculation history is deliberately one-to-many per Case. A second
        # calculation must persist instead of replacing or violating a unique key.
        db.add_all(
            [
                Calculation(case_id=case_a, formula_version="pytest-v1"),
                Calculation(case_id=case_a, formula_version="pytest-v2"),
            ]
        )
        await db.commit()
        calculation_count = await db.scalar(
            select(func.count(Calculation.id)).where(Calculation.case_id == case_a)
        )
        assert calculation_count == 2


def test_same_source_operation_is_idempotent_under_postgres_concurrency() -> None:
    asyncio.run(_scenario_same_operation_is_exactly_once())


def test_distinct_source_operations_remain_distinct_cases_under_postgres_concurrency() -> None:
    asyncio.run(_scenario_distinct_operations_create_distinct_active_cases())
