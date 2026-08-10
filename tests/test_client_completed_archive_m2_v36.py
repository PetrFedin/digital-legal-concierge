from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cases.case_service import CaseService
from app.domain.cases.client_case_scope import (
    active_or_latest_completed_case_for_user,
    active_or_latest_completed_m1_case_for_user,
    latest_completed_case_for_user,
)
from app.domain.statuses.case_statuses import CaseStatus
from app.models import Base
from app.models.case import Case
from app.models.user import User


async def create_database(tmp_path, name: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, factory


async def create_user(session, *, suffix: int) -> User:
    user = User(
        telegram_id=994000 + suffix,
        full_name=f"Клиент archive {suffix}",
    )
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_closed_m2_is_available_in_shared_read_only_archive(tmp_path):
    engine, factory = await create_database(tmp_path, "m2-completed-archive.db")
    async with factory() as session:
        user = await create_user(session, suffix=1)
        closed_at = datetime.now(timezone.utc) - timedelta(hours=1)
        case = Case(
            case_number="M2-ARCHIVE-1",
            client_id=user.id,
            route="M2",
            status=CaseStatus.M2_CLOSED,
            title="Завершённая консультация",
            next_action="Действий не требуется",
            closed_at=closed_at,
        )
        session.add(case)
        await session.commit()

        completed = await latest_completed_case_for_user(
            session,
            user_id=user.id,
        )
        assert completed is not None
        assert completed.id == case.id
        assert str(completed.status) == str(CaseStatus.M2_CLOSED)

        resolved, is_completed = await active_or_latest_completed_case_for_user(
            session,
            case_service=CaseService(session),
            user_id=user.id,
        )
        assert resolved is not None
        assert resolved.id == case.id
        assert is_completed is True

        # Older Telegram read-only screens still import the historical M1-named
        # helper. It must now resolve the same shared archive so closed M2 does
        # not disappear from Payments/Documents/History.
        legacy_resolved, legacy_completed = (
            await active_or_latest_completed_m1_case_for_user(
                session,
                case_service=CaseService(session),
                user_id=user.id,
            )
        )
        assert legacy_resolved is not None
        assert legacy_resolved.id == case.id
        assert legacy_completed is True

    await engine.dispose()


@pytest.mark.asyncio
async def test_active_case_wins_over_completed_archive(tmp_path):
    engine, factory = await create_database(tmp_path, "active-wins-archive.db")
    async with factory() as session:
        user = await create_user(session, suffix=2)
        session.add_all(
            [
                Case(
                    case_number="M2-ARCHIVE-OLD",
                    client_id=user.id,
                    route="M2",
                    status=CaseStatus.M2_CLOSED,
                    title="Архивная консультация",
                    closed_at=datetime.now(timezone.utc) - timedelta(days=1),
                ),
                Case(
                    case_number="M1-ACTIVE-NEW",
                    client_id=user.id,
                    route="M1",
                    status=CaseStatus.M1_LAWYER_REVIEW,
                    title="Активное дело",
                    next_action="Ожидать проверку документов",
                ),
            ]
        )
        await session.commit()

        resolved, is_completed = await active_or_latest_completed_case_for_user(
            session,
            case_service=CaseService(session),
            user_id=user.id,
        )
        assert resolved is not None
        assert resolved.case_number == "M1-ACTIVE-NEW"
        assert is_completed is False

    await engine.dispose()


@pytest.mark.asyncio
async def test_latest_completed_case_can_be_m1_or_m2_by_close_time(tmp_path):
    engine, factory = await create_database(tmp_path, "latest-completed-route.db")
    async with factory() as session:
        user = await create_user(session, suffix=3)
        now = datetime.now(timezone.utc)
        older_m1 = Case(
            case_number="M1-CLOSED-OLDER",
            client_id=user.id,
            route="M1",
            status=CaseStatus.M1_CLOSED,
            title="Старое завершённое дело",
            closed_at=now - timedelta(days=2),
        )
        newer_m2 = Case(
            case_number="M2-CLOSED-NEWER",
            client_id=user.id,
            route="M2",
            status=CaseStatus.M2_CLOSED,
            title="Последняя завершённая консультация",
            closed_at=now - timedelta(hours=2),
        )
        session.add_all([older_m1, newer_m2])
        await session.commit()

        completed = await latest_completed_case_for_user(
            session,
            user_id=user.id,
        )
        assert completed is not None
        assert completed.id == newer_m2.id
        assert completed.case_number == "M2-CLOSED-NEWER"

    await engine.dispose()


def test_m2_archive_source_contract_keeps_read_only_navigation_visible():
    source = Path("app/bot/screens/my_case.py").read_text(encoding="utf-8")
    assert "📁 ИТОГ КОНСУЛЬТАЦИИ" in source
    assert "Консультационный маршрут завершён" in source
    assert '("💳 Оплаты", "payments_open")' in source
    assert "payments_disabled" not in source
    assert '("📄 Документы обращения", "documents_open")' in source
    assert '("🕘 История обращения", "case_history_open")' in source
