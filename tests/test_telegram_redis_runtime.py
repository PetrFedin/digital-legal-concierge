from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import CallbackQuery, Chat, Message as TelegramMessage, Update, User as TelegramUser
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.bot.bot import build_dispatcher
from app.bot.calculator_draft import CALCULATOR_CASE_ID
from app.bot.states import CalculatorStates
from app.config import settings
from app.db.session import AsyncSessionLocal
from app.models.case import Case
from app.models.case_creation_request import CaseCreationRequest
from app.models.user import User


_REDIS_URL = str(settings.redis_url or "").strip()
pytestmark = pytest.mark.skipif(
    not _REDIS_URL or settings.fsm_storage_backend.strip().lower() != "redis",
    reason="Redis-backed Telegram runtime contract",
)


class RecordingTelegramSession(AiohttpSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[object] = []

    async def make_request(self, bot, method, timeout=None):  # noqa: ANN001
        self.calls.append(method)
        return True


def _telegram_id() -> int:
    return 6_000_000_000_000 + (uuid.uuid4().int % 1_000_000_000)


def _user(telegram_id: int) -> TelegramUser:
    return TelegramUser(
        id=telegram_id,
        is_bot=False,
        first_name="Redis",
        last_name="Runtime",
        username=f"redis_runtime_{telegram_id}",
    )


def _bot_message(telegram_id: int, message_id: int, text: str) -> TelegramMessage:
    return TelegramMessage(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type="private"),
        from_user=TelegramUser(id=999_000_002, is_bot=True, first_name="DLC"),
        text=text,
    )


def _callback_update(
    *,
    telegram_id: int,
    update_id: int,
    callback_id: str,
    message_id: int,
    data: str,
) -> Update:
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=callback_id,
            from_user=_user(telegram_id),
            chat_instance=f"redis-runtime-{telegram_id}",
            message=_bot_message(telegram_id, message_id, "🧮 Расчёт"),
            data=data,
        ),
    )


def _message_update(
    *,
    telegram_id: int,
    update_id: int,
    message_id: int,
    text: str,
) -> Update:
    return Update(
        update_id=update_id,
        message=TelegramMessage(
            message_id=message_id,
            date=datetime.now(timezone.utc),
            chat=Chat(id=telegram_id, type="private"),
            from_user=_user(telegram_id),
            text=text,
        ),
    )


async def _user_and_case_counts(telegram_id: int) -> tuple[int, int, int]:
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.telegram_id == telegram_id))
        assert user is not None
        case_count = await db.scalar(
            select(func.count(Case.id)).where(Case.client_id == int(user.id))
        )
        request_count = await db.scalar(
            select(func.count(CaseCreationRequest.id)).where(
                CaseCreationRequest.client_id == int(user.id)
            )
        )
        case_id = await db.scalar(
            select(Case.id)
            .where(Case.client_id == int(user.id))
            .order_by(Case.id.desc())
            .limit(1)
        )
        assert case_id is not None
        return int(case_count or 0), int(request_count or 0), int(case_id)


@pytest.mark.asyncio
async def test_redis_fsm_survives_connection_restart_and_db_recovers_after_state_loss() -> None:
    redis = Redis.from_url(_REDIS_URL)
    await redis.flushdb()
    await redis.aclose()

    telegram_id = _telegram_id()
    callback_id = f"redis-calc-{uuid.uuid4().hex}"
    dispatcher = build_dispatcher()
    session = RecordingTelegramSession()
    bot = Bot(token="123456789:" + ("R" * 35), session=session)

    try:
        await dispatcher.feed_update(
            bot,
            _callback_update(
                telegram_id=telegram_id,
                update_id=1,
                callback_id=callback_id,
                message_id=1001,
                data="calc_start",
            ),
        )
        await dispatcher.feed_update(
            bot,
            _message_update(
                telegram_id=telegram_id,
                update_id=2,
                message_id=1002,
                text="8500000",
            ),
        )

        before_restart = dispatcher.fsm.get_context(
            bot=bot,
            chat_id=telegram_id,
            user_id=telegram_id,
        )
        before_data = await before_restart.get_data()
        assert await before_restart.get_state() == CalculatorStates.waiting_planned_transfer_date.state
        assert before_data["contract_price"] == "8500000.00"
        case_id = int(before_data[CALCULATOR_CASE_ID])

        # A bot process restart creates a new Redis connection. Replacing the
        # storage connection exercises the same persisted keys without rebuilding
        # global aiogram routers inside one Python interpreter (routers are
        # intentionally single-parent objects).
        previous_storage = dispatcher.fsm.storage
        await previous_storage.close()
        dispatcher.fsm.storage = RedisStorage.from_url(_REDIS_URL)

        after_restart = dispatcher.fsm.get_context(
            bot=bot,
            chat_id=telegram_id,
            user_id=telegram_id,
        )
        after_data = await after_restart.get_data()
        assert await after_restart.get_state() == CalculatorStates.waiting_planned_transfer_date.state
        assert after_data["contract_price"] == "8500000.00"
        assert int(after_data[CALCULATOR_CASE_ID]) == case_id

        await dispatcher.feed_update(
            bot,
            _message_update(
                telegram_id=telegram_id,
                update_id=3,
                message_id=1003,
                text="01.01.2025",
            ),
        )
        continued = dispatcher.fsm.get_context(
            bot=bot,
            chat_id=telegram_id,
            user_id=telegram_id,
        )
        continued_data = await continued.get_data()
        assert await continued.get_state() == CalculatorStates.waiting_object_transfer_status.state
        assert continued_data["planned_transfer_date"] == "2025-01-01"
        assert int(continued_data[CALCULATOR_CASE_ID]) == case_id

        case_count, request_count, persisted_case_id = await _user_and_case_counts(telegram_id)
        assert (case_count, request_count, persisted_case_id) == (1, 1, case_id)

        # Redis is operational state, not the legal source of truth. If FSM keys
        # disappear, replay of the same Telegram source event must resolve the
        # existing Case through CaseCreationRequest and re-bind a fresh draft.
        redis = Redis.from_url(_REDIS_URL)
        await redis.flushdb()
        await redis.aclose()

        missing = dispatcher.fsm.get_context(
            bot=bot,
            chat_id=telegram_id,
            user_id=telegram_id,
        )
        assert await missing.get_state() is None
        assert await missing.get_data() == {}

        await dispatcher.feed_update(
            bot,
            _callback_update(
                telegram_id=telegram_id,
                update_id=4,
                callback_id=callback_id,
                message_id=1004,
                data="calc_start",
            ),
        )

        recovered = dispatcher.fsm.get_context(
            bot=bot,
            chat_id=telegram_id,
            user_id=telegram_id,
        )
        recovered_data = await recovered.get_data()
        assert await recovered.get_state() == CalculatorStates.waiting_contract_price.state
        assert int(recovered_data[CALCULATOR_CASE_ID]) == case_id

        case_count, request_count, persisted_case_id = await _user_and_case_counts(telegram_id)
        assert (case_count, request_count, persisted_case_id) == (1, 1, case_id)
    finally:
        await dispatcher.fsm.close()
        await bot.session.close()
