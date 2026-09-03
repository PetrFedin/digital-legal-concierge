from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.screens.client_message_recovery import (
    retarget_preserved_draft_to_current_case,
)
from app.bot.screens.consultation_booking_ui import consultation_action_center
from app.bot.screens.message_history_guard import present_message_history
from app.bot.screens.payment_stage_binding_guard import (
    legacy_stage_payment_is_confirmation_only,
)
from app.bot.screens.post_calculation import continue_m1_after_calculation
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
    def __init__(self, data: dict | None = None) -> None:
        self.data = dict(data or {})
        self.state = None

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)
        return dict(self.data)

    async def set_state(self, state):
        self.state = state

    async def clear(self):
        self.data.clear()
        self.state = None


def _callback_values(markup) -> set[str]:
    if markup is None:
        return set()
    return {
        str(button.callback_data)
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    }


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
            user_id = int(user.id)
            case_a = Case(
                case_number="BOUNDARY-M1-A",
                client_id=user_id,
                route="M1",
                status="M1_DOCUMENTS_PENDING",
            )
            case_b = Case(
                case_number="BOUNDARY-M2-B",
                client_id=user_id,
                route="M2",
                status="M2_DESCRIPTION_PENDING",
            )
            db.add_all([case_a, case_b])
            await db.flush()
            db.add(
                ClientCaseContext(
                    client_id=user_id,
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
            is_read = await db.scalar(
                select(Message.is_read).where(Message.case_id == case_a_id)
            )
            assert is_read is False

        assert callback.message.text is not None
        assert "BOUNDARY-M1-A" in callback.message.text
        assert "19.08.2026 15:30 МСК" in callback.message.text

        # Once the client explicitly selects Case A, the same presentation path
        # can safely mark the visible lawyer message as read after its rollback.
        async with session_factory() as db:
            context = await db.get(ClientCaseContext, user_id)
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


def test_legacy_payment_confirmation_snapshots_case_before_rollback() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        telegram_id = 990000203
        async with session_factory() as db:
            user = User(telegram_id=telegram_id, full_name="Payment Boundary")
            db.add(user)
            await db.flush()
            case = Case(
                case_number="BOUNDARY-PAY-1",
                client_id=user.id,
                route="M1",
                status="M1_WAITING_PAYMENT_30000",
            )
            db.add(case)
            await db.commit()
            case_id = int(case.id)

        callback = _FakeCallback(telegram_id, data="pay_start_30000")
        async with session_factory() as db:
            await legacy_stage_payment_is_confirmation_only(callback, db)

        assert callback.message.text is not None
        assert "BOUNDARY-PAY-1" in callback.message.text
        assert (
            f"pay_stage:v2:{case_id}:M1_INITIAL_PAYMENT"
            in _callback_values(callback.message.reply_markup)
        )
        await engine.dispose()

    asyncio.run(scenario())


def test_message_draft_retarget_snapshots_case_before_rollback() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        telegram_id = 990000204
        async with session_factory() as db:
            user = User(telegram_id=telegram_id, full_name="Draft Boundary")
            db.add(user)
            await db.flush()
            case = Case(
                case_number="BOUNDARY-DRAFT-1",
                client_id=user.id,
                route="M1",
                status="M1_DOCUMENTS_PENDING",
            )
            db.add(case)
            await db.flush()
            db.add(
                ClientCaseContext(
                    client_id=user.id,
                    selected_case_id=case.id,
                )
            )
            await db.commit()
            case_id = int(case.id)

        state = _FakeState(
            {
                "draft_text": "Сохранённый вопрос",
                "source_message_id": 777,
                "category": "Ход дела",
                "urgency": "Обычный",
            }
        )
        callback = _FakeCallback(
            telegram_id,
            data=f"message_retarget_current:v2:{case_id}",
        )
        async with session_factory() as db:
            await retarget_preserved_draft_to_current_case(callback, state, db)

        assert state.data["case_id"] == case_id
        assert state.data["case_number"] == "BOUNDARY-DRAFT-1"
        assert state.data["client_message_case_id"] == case_id
        assert callback.message.text is not None
        assert "Сохранённый вопрос" in callback.message.text
        await engine.dispose()

    asyncio.run(scenario())


def test_stale_post_calculation_recovery_uses_status_snapshot_after_rollback() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        telegram_id = 990000205
        async with session_factory() as db:
            user = User(telegram_id=telegram_id, full_name="Decision Boundary")
            db.add(user)
            await db.flush()
            case = Case(
                case_number="BOUNDARY-DECISION-1",
                client_id=user.id,
                route="M1",
                status="M1_DOCUMENTS_PENDING",
            )
            db.add(case)
            await db.commit()
            case_id = int(case.id)

        callback = _FakeCallback(
            telegram_id,
            data=f"calc_continue_m1:v2:{case_id}",
        )
        async with session_factory() as db:
            await continue_m1_after_calculation(callback, db)

        assert callback.message.text is not None
        assert "Ведение дела уже начато" in callback.message.text
        await engine.dispose()

    asyncio.run(scenario())
