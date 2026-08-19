from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.screens.consultation_booking_ui import consultation_action_center
from app.bot.screens.message_history_guard import present_message_history
from app.models import Base
from app.models.case import Case
from app.models.client_case_context import ClientCaseContext
from app.models.message import Message
from app.models.user import User


class _FakeMessage:
    def __init__(self) -> None:
        self.text: str | None = None
        self.reply_markup = None

    async def edit_text(self, text: str, *, reply_markup=None):
        self.text = text
        self.reply_markup = reply_markup
        return self

    async def answer(self, text: str, *, reply_markup=None):
        self.text = text
        self.reply_markup = reply_markup
        return self


class _FakeCallback:
    def __init__(self, telegram_id: int, *, data: str) -> None:
        self.from_user = SimpleNamespace(
            id=telegram_id,
            username=None,
            full_name="Boundary Test",
        )
        self.message = _FakeMessage()
        self.data = data

    async def answer(self, *args, **kwargs):
        return None


class _FakeState:
    async def get_data(self):
        return {}


def test_completed_m2_action_center_does_not_touch_orm_after_rollback() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        telegram_id = 990000201
        async with session_factory() as db:
            user = User(telegram_id=telegram_id, full_name="Boundary Test")
            db.add(user)
            await db.flush()
            db.add(
                Case(
                    case_number="BOUNDARY-M2-1",
                    client_id=user.id,
                    route="M2",
                    status="M2_CLOSED",
                    close_reason="M2_CONSULTATION_COMPLETED",
                )
            )
            await db.commit()

        callback = _FakeCallback(
            telegram_id,
            data="consultation_booked_open",
        )
        async with session_factory() as db:
            # The screen intentionally calls rollback() after snapshotting the
            # completed Case number. Any later ORM attribute access would require
            # implicit async I/O and can surface as MissingGreenlet in production.
            await consultation_action_center(callback, db)

        assert callback.message.text is not None
        assert "BOUNDARY-M2-1" in callback.message.text
        assert "ЗАВЕРШЕНА" in callback.message.text
        await engine.dispose()

    asyncio.run(scenario())


def test_message_history_snapshots_case_before_rollback_and_binds_read_tracking() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        telegram_id = 990000202
        async with session_factory() as db:
            user = User(telegram_id=telegram_id, full_name="Boundary Test")
            db.add(user)
            await db.flush()
            case_a = Case(
                case_number="BOUNDARY-M1-A",
                client_id=user.id,
                route="M1",
                status="M1_DOCUMENTS_PENDING",
            )
            case_b = Case(
                case_number="BOUNDARY-M2-B",
                client_id=user.id,
                route="M2",
                status="M2_DESCRIPTION_PENDING",
            )
            db.add_all([case_a, case_b])
            await db.flush()
            db.add(
                ClientCaseContext(
                    client_id=user.id,
                    selected_case_id=case_b.id,
                )
            )
            db.add(
                Message(
                    case_id=case_a.id,
                    sender_type="lawyer",
                    sender_id=501,
                    text="Сообщение по первому обращению",
                    is_read=False,
                    created_at=datetime(2026, 8, 19, 12, 30, tzinfo=timezone.utc),
                )
            )
            await db.commit()
            case_a_id = int(case_a.id)
            case_b_id = int(case_b.id)

        # Exact Case A history is safe to display while Case B is selected, but
        # read tracking is a mutation and therefore must not touch Case A yet.
        callback = _FakeCallback(
            telegram_id,
            data=f"message_history:v2:{case_a_id}:0",
        )
        async with session_factory() as db:
            await present_message_history(callback, db, _FakeState())
            unread = await db.scalar(
                select(Message.is_read).where(Message.case_id == case_a_id)
            )
            assert unread is False

        assert callback.message.text is not None
        assert "BOUNDARY-M1-A" in callback.message.text
        assert "19.08.2026 15:30 МСК" in callback.message.text

        # Once the client explicitly selects Case A, the same presentation path
        # can safely mark the visible lawyer message as read after its rollback.
        async with session_factory() as db:
            context = await db.get(ClientCaseContext, telegram_id - telegram_id + 1)
            if context is None:
                user = await db.scalar(select(User).where(User.telegram_id == telegram_id))
                assert user is not None
                context = await db.get(ClientCaseContext, int(user.id))
            assert context is not None
            context.selected_case_id = case_a_id
            await db.commit()

        callback = _FakeCallback(
            telegram_id,
            data=f"message_history:v2:{case_a_id}:0",
        )
        async with session_factory() as db:
            await present_message_history(callback, db, _FakeState())
            is_read = await db.scalar(
                select(Message.is_read).where(Message.case_id == case_a_id)
            )
            assert is_read is True

        assert case_b_id != case_a_id
        await engine.dispose()

    asyncio.run(scenario())
