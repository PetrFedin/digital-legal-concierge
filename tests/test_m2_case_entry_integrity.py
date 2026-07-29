from __future__ import annotations

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cases.case_service import CaseService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.user import User


@pytest.fixture
async def m2_entry_db(tmp_path):
    database_path = tmp_path / "m2-case-entry-integrity.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def seed_user(session, *, suffix: int) -> User:
    user = User(
        telegram_id=1_040_000 + suffix,
        telegram_username=f"m2_entry_{suffix}",
        full_name=f"Клиент {suffix}",
    )
    session.add(user)
    await session.flush()
    return user


async def active_m2_count(session, *, client_id: int) -> int:
    return int(
        (
            await session.execute(
                select(func.count(Case.id)).where(
                    Case.client_id == client_id,
                    Case.route == RouteCode.M2.value,
                    Case.status.notin_(CaseService.CLOSED_STATUSES),
                )
            )
        ).scalar_one()
    )


async def case_created_count(session, *, client_id: int) -> int:
    return int(
        (
            await session.execute(
                select(func.count(AuditLog.id))
                .join(Case, Case.id == AuditLog.entity_id)
                .where(
                    AuditLog.entity_type == "case",
                    AuditLog.action == "CASE_CREATED",
                    Case.client_id == client_id,
                    Case.route == RouteCode.M2.value,
                )
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_repeated_m2_entry_reuses_same_case(m2_entry_db):
    async with m2_entry_db() as session:
        user = await seed_user(session, suffix=1)
        service = CaseService(session)

        first = await service.get_or_create_m2_case_for_user(user)
        second = await service.get_or_create_m2_case_for_user(user)
        await session.commit()

        assert first.id == second.id
        assert first.route == RouteCode.M2.value
        assert first.status == CaseStatus.M2_DESCRIPTION_PENDING.value
        assert await active_m2_count(session, client_id=user.id) == 1
        assert await case_created_count(session, client_id=user.id) == 1


@pytest.mark.asyncio
async def test_active_m1_is_preserved_when_m2_is_created(m2_entry_db):
    async with m2_entry_db() as session:
        user = await seed_user(session, suffix=2)
        m1 = Case(
            case_number="M1-PRESERVED",
            client_id=user.id,
            route=RouteCode.M1.value,
            status=CaseStatus.M1_LAWYER_REVIEW.value,
            title="Действующее ведение дела",
        )
        session.add(m1)
        await session.flush()

        m2 = await CaseService(session).get_or_create_m2_case_for_user(user)
        await session.commit()

        await session.refresh(m1)
        assert m1.route == RouteCode.M1.value
        assert m1.status == CaseStatus.M1_LAWYER_REVIEW.value
        assert m2.id != m1.id
        assert m2.route == RouteCode.M2.value
        assert await active_m2_count(session, client_id=user.id) == 1


@pytest.mark.asyncio
async def test_closed_m2_history_allows_new_active_case(m2_entry_db):
    async with m2_entry_db() as session:
        user = await seed_user(session, suffix=3)
        closed = Case(
            case_number="M2-CLOSED-HISTORY",
            client_id=user.id,
            route=RouteCode.M2.value,
            status=CaseStatus.M2_CLOSED.value,
            title="Завершённая консультация",
        )
        session.add(closed)
        await session.flush()

        current = await CaseService(session).get_or_create_m2_case_for_user(user)
        await session.commit()

        assert current.id != closed.id
        assert current.status == CaseStatus.M2_DESCRIPTION_PENDING.value
        assert await active_m2_count(session, client_id=user.id) == 1


@pytest.mark.asyncio
async def test_legacy_duplicate_active_m2_cases_stop_automatic_selection(m2_entry_db):
    async with m2_entry_db() as session:
        user = await seed_user(session, suffix=4)
        # Simulate a database created before uq_cases_active_m2_client.
        await session.execute(text("DROP INDEX uq_cases_active_m2_client"))
        session.add_all(
            [
                Case(
                    case_number="LEGACY-M2-ONE",
                    client_id=user.id,
                    route=RouteCode.M2.value,
                    status=CaseStatus.M2_DESCRIPTION_PENDING.value,
                    title="Legacy one",
                ),
                Case(
                    case_number="LEGACY-M2-TWO",
                    client_id=user.id,
                    route=RouteCode.M2.value,
                    status=CaseStatus.M2_SLOT_PENDING.value,
                    title="Legacy two",
                ),
            ]
        )
        await session.flush()

        with pytest.raises(RuntimeError, match="несколько активных"):
            await CaseService(session).get_or_create_m2_case_for_user(user)
