from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.context import BotContextService
from app.bot.screens.messages import MessageTargetChanged, _locked_message_target
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
        telegram_id=998000 + suffix,
        full_name=f"Клиент message snapshot {suffix}",
    )
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_message_draft_stays_bound_to_original_case_after_case_closes(tmp_path):
    engine, factory = await create_database(tmp_path, "message-target-closed.db")
    async with factory() as session:
        user = await create_user(session, suffix=1)
        case = Case(
            case_number="MSG-SNAPSHOT-1",
            client_id=user.id,
            route="M1",
            status=CaseStatus.M1_LAWYER_REVIEW,
            title="Исходное активное дело",
            next_action="Ожидать юриста",
        )
        session.add(case)
        await session.commit()

        ctx = BotContextService(session)
        target = await _locked_message_target(
            session,
            ctx,
            user,
            {"case_id": case.id, "new_request_confirmed": False},
        )
        assert target.id == case.id
        await session.rollback()

        case.status = CaseStatus.M1_CLOSED
        await session.commit()

        with pytest.raises(MessageTargetChanged, match="уже завершено"):
            await _locked_message_target(
                session,
                ctx,
                user,
                {"case_id": case.id, "new_request_confirmed": False},
            )
        await session.rollback()

        count = await session.scalar(select(func.count(Case.id)))
        assert count == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_new_message_case_requires_explicit_new_request_confirmation(tmp_path):
    engine, factory = await create_database(tmp_path, "message-target-confirm.db")
    async with factory() as session:
        user = await create_user(session, suffix=2)
        ctx = BotContextService(session)

        with pytest.raises(MessageTargetChanged, match="не было подтверждено"):
            await _locked_message_target(
                session,
                ctx,
                user,
                {"case_id": None, "new_request_confirmed": False},
            )
        await session.rollback()
        assert await session.scalar(select(func.count(Case.id))) == 0

        target = await _locked_message_target(
            session,
            ctx,
            user,
            {"case_id": None, "new_request_confirmed": True},
        )
        assert target.client_id == user.id
        assert str(target.status) == str(CaseStatus.DRAFT)
        assert await session.scalar(select(func.count(Case.id))) == 1
        await session.rollback()

    await engine.dispose()


def test_message_source_contract_preserves_draft_and_archive_read_only():
    source = Path("app/bot/screens/messages.py").read_text(encoding="utf-8")

    assert "with_for_update()" in source
    assert "_locked_message_target(db, ctx, user, data)" in source
    assert "new_request_confirmed" in source
    assert '"message_new_request"' in source
    assert '"message_retarget_new_confirm"' in source
    assert '"message_retarget_new"' in source
    assert "Черновик сохранён" in source
    assert "Архив переписки" in source
    assert "read_only=read_only" in source

    history_start = source.index("def _history_keyboard")
    history_end = source.index("async def _safe_edit", history_start)
    history_block = source[history_start:history_end]
    assert "if not read_only:" in history_block
    assert '"message_create"' in history_block

    submit_start = source.index("async def message_submit")
    submit_block = source[submit_start:]
    assert "except MessageTargetChanged" in submit_block
    assert "Черновик не будет автоматически перенесён" in submit_block
