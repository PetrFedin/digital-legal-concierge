from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.screens.consultation_booking_ui import consultation_action_center
from app.models import Base
from app.models.case import Case
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
    def __init__(self, telegram_id: int) -> None:
        self.from_user = SimpleNamespace(id=telegram_id)
        self.message = _FakeMessage()
        self.data = "consultation_booked_open"

    async def answer(self, *args, **kwargs):
        return None


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

        callback = _FakeCallback(telegram_id)
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
