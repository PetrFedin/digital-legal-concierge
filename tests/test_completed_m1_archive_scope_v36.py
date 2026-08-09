from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cases.case_service import CaseService
from app.domain.cases.client_case_scope import (
    active_or_latest_completed_m1_case_for_user,
    latest_completed_m1_case_for_user,
)
from app.domain.statuses.case_statuses import CaseStatus
from app.models import Base
from app.models.case import Case
from app.models.user import User


@asynccontextmanager
async def database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'completed-scope-v36.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield factory
    finally:
        await engine.dispose()


async def seed_user(session):
    user = User(telegram_id=983301, full_name="Клиент Архив")
    session.add(user)
    await session.flush()
    return user


def closed_case(*, user_id: int, number: str, closed_at: datetime) -> Case:
    return Case(
        case_number=number,
        client_id=user_id,
        route="M1",
        status=CaseStatus.M1_CLOSED,
        title="Завершённое дело",
        closed_at=closed_at,
    )


@pytest.mark.asyncio
async def test_latest_completed_scope_selects_newest_closed_case(tmp_path):
    async with database(tmp_path) as factory:
        async with factory() as session:
            user = await seed_user(session)
            now = datetime.now(timezone.utc)
            older = closed_case(
                user_id=user.id,
                number="M1-ARCHIVE-OLD",
                closed_at=now - timedelta(days=5),
            )
            newer = closed_case(
                user_id=user.id,
                number="M1-ARCHIVE-NEW",
                closed_at=now - timedelta(days=1),
            )
            session.add_all([older, newer])
            await session.flush()

            selected = await latest_completed_m1_case_for_user(
                session,
                user_id=user.id,
            )
            resolved, completed = await active_or_latest_completed_m1_case_for_user(
                session,
                case_service=CaseService(session),
                user_id=user.id,
            )

            assert selected is newer
            assert resolved is newer
            assert completed is True


@pytest.mark.asyncio
async def test_active_case_wins_and_closed_case_never_becomes_mutation_scope(tmp_path):
    async with database(tmp_path) as factory:
        async with factory() as session:
            user = await seed_user(session)
            closed = closed_case(
                user_id=user.id,
                number="M1-ARCHIVE-CLOSED",
                closed_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
            session.add(closed)
            await session.flush()

            service = CaseService(session)
            assert await service.get_active_case_for_user(user.id) is None

            archived, completed = await active_or_latest_completed_m1_case_for_user(
                session,
                case_service=service,
                user_id=user.id,
            )
            assert archived is closed
            assert completed is True

            active = Case(
                case_number="M1-ARCHIVE-ACTIVE",
                client_id=user.id,
                route="M1",
                status=CaseStatus.M1_ENFORCEMENT,
                title="Активное исполнение",
            )
            session.add(active)
            await session.flush()

            resolved, completed = await active_or_latest_completed_m1_case_for_user(
                session,
                case_service=service,
                user_id=user.id,
            )
            assert resolved is active
            assert completed is False
