from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from aiogram.types import Chat, Message, User as TelegramUser
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.screens.reply_menu_direct import _message_context
from app.models import Base
from app.models.case import Case
from app.models.client_case_context import ClientCaseContext
from app.models.user import User


def _telegram_message(telegram_id: int) -> Message:
    return Message(
        message_id=501,
        date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type="private"),
        from_user=TelegramUser(
            id=telegram_id,
            is_bot=False,
            first_name="Case Context",
        ),
        text="📄 Документы",
    )


def test_reply_menu_does_not_guess_newest_case_after_selected_case_closes() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        telegram_id = 990000301
        async with session_factory() as db:
            user = User(
                telegram_id=telegram_id,
                full_name="Case Context Client",
            )
            db.add(user)
            await db.flush()

            first = Case(
                case_number="CTX-A",
                client_id=user.id,
                status="CALCULATOR_STARTED",
            )
            second = Case(
                case_number="CTX-B",
                client_id=user.id,
                status="CALCULATOR_STARTED",
            )
            formerly_selected = Case(
                case_number="CTX-C",
                client_id=user.id,
                route="M1",
                status="M1_CLOSED",
            )
            db.add_all([first, second, formerly_selected])
            await db.flush()
            db.add(
                ClientCaseContext(
                    client_id=user.id,
                    selected_case_id=formerly_selected.id,
                )
            )
            await db.commit()

            # Two active matters remain, but persisted selection points to the
            # completed third matter. A persistent reply-menu action must not
            # silently choose whichever active Case happens to be newest.
            _ctx, resolved_user, resolved_case = await _message_context(
                _telegram_message(telegram_id),
                db,
            )
            assert int(resolved_user.id) == int(user.id)
            assert resolved_case is None

            # Once the client explicitly selects one active Case, the same
            # resolver must return exactly that context.
            context = await db.get(ClientCaseContext, int(user.id))
            assert context is not None
            context.selected_case_id = int(first.id)
            await db.commit()

            _ctx, _resolved_user, resolved_case = await _message_context(
                _telegram_message(telegram_id),
                db,
            )
            assert resolved_case is not None
            assert int(resolved_case.id) == int(first.id)
            assert str(resolved_case.case_number) == "CTX-A"

        await engine.dispose()

    asyncio.run(scenario())
