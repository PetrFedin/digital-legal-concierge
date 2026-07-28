from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.screens.consultation_entry import _get_or_create_m2_case
from app.domain.statuses.case_statuses import CLOSED_CASE_STATUSES, RouteCode
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation


class FakeCallback:
    def __init__(self, telegram_id: int):
        self.from_user = SimpleNamespace(
            id=telegram_id,
            username=f"entry_{telegram_id}",
            full_name=f"Клиент {telegram_id}",
        )


@pytest.fixture
async def entry_contract_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'entry-contract.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


def test_entry_source_locks_user_before_active_case_lookup():
    source = Path("app/bot/screens/consultation_entry.py").read_text(
        encoding="utf-8"
    )

    user_lock = source.index(".with_for_update()")
    active_case_query = source.index("Case.client_id == user.id")
    assert user_lock < active_case_query
    assert "async with db.begin_nested()" in source
    assert "except IntegrityError" in source


@pytest.mark.asyncio
async def test_repeated_entry_reuses_single_active_m2_route(entry_contract_db):
    async with entry_contract_db() as session:
        callback = FakeCallback(1_009_001)

        _, first_case, first_consultation = await _get_or_create_m2_case(
            callback,
            session,
        )
        await session.commit()
        _, second_case, second_consultation = await _get_or_create_m2_case(
            callback,
            session,
        )
        await session.commit()

        assert second_case.id == first_case.id
        assert second_consultation.id == first_consultation.id
        assert (
            await session.scalar(
                select(func.count(Case.id)).where(
                    Case.client_id == first_case.client_id,
                    Case.route == RouteCode.M2.value,
                    Case.status.notin_(CLOSED_CASE_STATUSES),
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(Consultation.id)).where(
                    Consultation.case_id == first_case.id
                )
            )
            == 1
        )
