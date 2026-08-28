from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cases.case_transition_policy import (
    CaseTransitionError,
    transition_allowed,
    validate_initial_status,
    validate_transition,
)
from app.domain.statuses.case_statuses import CaseStatus
from app.models import Base, Case


def test_legacy_m2_status_remains_readable_and_can_only_move_forward() -> None:
    source, destination = validate_transition(
        CaseStatus.M2_CONSULTATION_ROUTE,
        CaseStatus.M2_DESCRIPTION_PENDING,
        force=False,
        actor_type="system",
        comment="legacy intake upgrade",
    )

    assert source == CaseStatus.M2_CONSULTATION_ROUTE
    assert destination == CaseStatus.M2_DESCRIPTION_PENDING
    assert transition_allowed(
        CaseStatus.M2_CONSULTATION_ROUTE,
        CaseStatus.M2_DESCRIPTION_PENDING,
    )


def test_new_case_cannot_start_in_legacy_m2_status() -> None:
    with pytest.raises(CaseTransitionError, match="исторических данных"):
        validate_initial_status(CaseStatus.M2_CONSULTATION_ROUTE)

    assert (
        validate_initial_status(CaseStatus.M2_DESCRIPTION_PENDING)
        == CaseStatus.M2_DESCRIPTION_PENDING
    )


@pytest.mark.parametrize("force", [False, True])
def test_transition_cannot_reenter_legacy_m2_status_even_when_forced(force: bool) -> None:
    with pytest.raises(CaseTransitionError, match="compatibility-only"):
        validate_transition(
            CaseStatus.M2_DESCRIPTION_PENDING,
            CaseStatus.M2_CONSULTATION_ROUTE,
            force=force,
            actor_type="admin" if force else "system",
            comment="administrative correction",
        )

    assert not transition_allowed(
        CaseStatus.M2_DESCRIPTION_PENDING,
        CaseStatus.M2_CONSULTATION_ROUTE,
    )


def test_orm_backstop_rejects_new_legacy_m2_status() -> None:
    with pytest.raises(ValueError, match="только для чтения"):
        Case(
            case_number="DLC-TEST-LEGACY",
            client_id=1,
            route="M2",
            status=CaseStatus.M2_CONSULTATION_ROUTE,
        )


def test_orm_backstop_rejects_direct_reentry_into_legacy_m2_status() -> None:
    case = Case(
        case_number="DLC-TEST-CURRENT",
        client_id=1,
        route="M2",
        status=CaseStatus.M2_DESCRIPTION_PENDING,
    )

    with pytest.raises(ValueError, match="повторный вход"):
        case.status = CaseStatus.M2_CONSULTATION_ROUTE


def test_historical_legacy_m2_database_row_hydrates_and_moves_forward() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                await session.execute(
                    Case.__table__.insert().values(
                        id=9_900_031,
                        case_number="DLC-LEGACY-M2-READ",
                        client_id=1,
                        route="M2",
                        status=CaseStatus.M2_CONSULTATION_ROUTE.value,
                    )
                )
                await session.commit()

            async with session_factory() as session:
                case = await session.get(Case, 9_900_031)
                assert case is not None
                assert case.status == CaseStatus.M2_CONSULTATION_ROUTE.value

                case.status = CaseStatus.M2_DESCRIPTION_PENDING.value
                await session.commit()

            async with session_factory() as session:
                upgraded = await session.get(Case, 9_900_031)
                assert upgraded is not None
                assert upgraded.status == CaseStatus.M2_DESCRIPTION_PENDING.value
        finally:
            await engine.dispose()

    asyncio.run(scenario())
