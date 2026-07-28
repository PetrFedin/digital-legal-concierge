from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.screens.consultation_entry import _get_or_create_m2_case
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.user import User


class FakeCallback:
    def __init__(self, telegram_id: int):
        self.from_user = SimpleNamespace(
            id=telegram_id,
            username=f"repeat_{telegram_id}",
            full_name=f"Клиент {telegram_id}",
        )


@pytest.fixture
async def entry_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'consultation-entry.db'}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_closed_m2_case_does_not_block_new_unique_case(entry_db):
    async with entry_db() as session:
        user = User(
            telegram_id=1_005_001,
            full_name="Повторный клиент",
        )
        session.add(user)
        await session.flush()
        closed = Case(
            case_number=f"M2-{user.id}",
            client_id=user.id,
            route=RouteCode.M2.value,
            status=CaseStatus.M2_CLOSED.value,
            title="Закрытая консультация",
        )
        session.add(closed)
        await session.flush()
        session.add(
            Consultation(
                case_id=closed.id,
                status=ConsultationStatus.CLOSED.value,
            )
        )
        await session.commit()

        callback = FakeCallback(user.telegram_id)
        _, new_case, consultation = await _get_or_create_m2_case(
            callback,
            session,
        )
        await session.commit()

        assert new_case.id != closed.id
        assert new_case.case_number != closed.case_number
        assert new_case.case_number.startswith(f"M2-{user.id}-")
        assert new_case.status == CaseStatus.M2_DESCRIPTION_PENDING.value
        assert consultation.case_id == new_case.id
        assert consultation.status == ConsultationStatus.DESCRIPTION_PENDING.value
        assert (
            await session.scalar(
                select(func.count(Case.id)).where(
                    Case.client_id == user.id,
                    Case.route == RouteCode.M2.value,
                )
            )
            == 2
        )


@pytest.mark.asyncio
async def test_reopening_active_m2_flow_is_idempotent(entry_db):
    async with entry_db() as session:
        callback = FakeCallback(1_005_002)

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
        assert second_case.case_number == first_case.case_number
        assert second_consultation.id == first_consultation.id
        assert (
            await session.scalar(
                select(func.count(Case.id)).where(
                    Case.client_id == first_case.client_id,
                    Case.route == RouteCode.M2.value,
                    Case.status.notin_({CaseStatus.M2_CLOSED.value}),
                )
            )
            == 1
        )
