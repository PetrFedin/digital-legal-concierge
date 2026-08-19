from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import CallbackQuery, Chat, Message as TelegramMessage, Update, User as TelegramUser
from sqlalchemy import func, select

from app.bot.bot import build_dispatcher
from app.bot.calculator_draft import CALCULATOR_CASE_ID
from app.config import settings
from app.db.session import AsyncSessionLocal
from app.domain.cases.case_service import CaseService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.models.case import Case
from app.models.case_creation_request import CaseCreationRequest
from app.models.client_case_context import ClientCaseContext
from app.models.message import Message as CaseMessage
from app.models.payment import Payment
from app.models.user import User


class RecordingTelegramSession(AiohttpSession):
    """Exercise real aiogram handlers while keeping Telegram network I/O local."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[object] = []

    async def make_request(self, bot, method, timeout=None):  # noqa: ANN001
        self.calls.append(method)
        return True


def _telegram_id() -> int:
    return 7_000_000_000_000 + (uuid.uuid4().int % 1_000_000_000)


def _telegram_user(telegram_id: int) -> TelegramUser:
    return TelegramUser(
        id=telegram_id,
        is_bot=False,
        first_name="Runtime",
        last_name="Client",
        username=f"runtime_{telegram_id}",
    )


def _bot_message(*, telegram_id: int, message_id: int, text: str) -> TelegramMessage:
    return TelegramMessage(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type="private"),
        from_user=TelegramUser(id=999_000_001, is_bot=True, first_name="DLC"),
        text=text,
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
            from_user=_telegram_user(telegram_id),
            text=text,
        ),
    )


def _callback_update(
    *,
    telegram_id: int,
    update_id: int,
    callback_id: str,
    message_id: int,
    data: str,
    message_text: str = "Актуальный экран дела",
) -> Update:
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=callback_id,
            from_user=_telegram_user(telegram_id),
            chat_instance=f"runtime-{telegram_id}",
            message=_bot_message(
                telegram_id=telegram_id,
                message_id=message_id,
                text=message_text,
            ),
            data=data,
        ),
    )


def _texts(session: RecordingTelegramSession) -> list[str]:
    return [
        str(getattr(method, "text", "") or "")
        for method in session.calls
        if getattr(method, "text", None) is not None
    ]


async def _seed_cases(
    telegram_id: int,
    specs: list[tuple[CaseStatus, str | None]],
) -> tuple[int, list[tuple[int, str]]]:
    async with AsyncSessionLocal() as db:
        user = User(
            telegram_id=telegram_id,
            telegram_username=f"runtime_{telegram_id}",
            full_name="Runtime Client",
        )
        db.add(user)
        await db.flush()

        created: list[tuple[int, str]] = []
        service = CaseService(db)
        for index, (status, route) in enumerate(specs, start=1):
            case = await service.create_case_for_operation(
                client=user,
                operation_key=f"pytest:telegram-runtime:{uuid.uuid4().hex}:{index}",
                purpose="runtime_seed",
                route=route,
                status=status,
                title=f"Runtime matter {index}",
            )
            created.append((int(case.id), str(case.case_number)))
        await db.commit()
        return int(user.id), created


async def _selected_case_id(user_id: int) -> int | None:
    async with AsyncSessionLocal() as db:
        context = await db.scalar(
            select(ClientCaseContext).where(ClientCaseContext.client_id == int(user_id))
        )
        return int(context.selected_case_id) if context is not None else None


async def _case_ids(user_id: int) -> list[int]:
    async with AsyncSessionLocal() as db:
        return list(
            (
                await db.execute(
                    select(Case.id)
                    .where(Case.client_id == int(user_id))
                    .order_by(Case.id.asc())
                )
            ).scalars().all()
        )


def _runtime_dispatcher(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "fsm_storage_backend", "memory")
    return build_dispatcher()


@pytest.mark.asyncio
async def test_persistent_calculate_creates_distinct_case_and_replay_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram_id = _telegram_id()
    user_id, seeded = await _seed_cases(
        telegram_id,
        [(CaseStatus.CALCULATOR_STARTED, None)],
    )
    first_case_id = seeded[0][0]

    dispatcher = _runtime_dispatcher(monkeypatch)
    session = RecordingTelegramSession()
    bot = Bot(token="123456789:" + ("A" * 35), session=session)
    try:
        await dispatcher.feed_update(
            bot,
            _message_update(
                telegram_id=telegram_id,
                update_id=1,
                message_id=101,
                text="🧮 Рассчитать неустойку",
            ),
        )
        assert await _case_ids(user_id) == [first_case_id]
        assert any(
            "будет создано отдельное обращение" in text
            for text in _texts(session)
        )

        session.calls.clear()
        callback_id = f"runtime-calc-{uuid.uuid4().hex}"
        await dispatcher.feed_update(
            bot,
            _callback_update(
                telegram_id=telegram_id,
                update_id=2,
                callback_id=callback_id,
                message_id=102,
                data="calc_start",
                message_text="🧮 НОВЫЙ РАСЧЁТ",
            ),
        )

        case_ids = await _case_ids(user_id)
        assert len(case_ids) == 2
        assert first_case_id in case_ids
        second_case_id = next(case_id for case_id in case_ids if case_id != first_case_id)
        assert await _selected_case_id(user_id) == second_case_id

        state = await dispatcher.fsm.get_context(
            bot=bot,
            chat_id=telegram_id,
            user_id=telegram_id,
        )
        data = await state.get_data()
        assert int(data[CALCULATOR_CASE_ID]) == second_case_id

        # Simulate lost FSM state/process memory, then replay the exact Telegram
        # callback. CaseCreationRequest must still deduplicate the source event.
        await state.clear()
        await dispatcher.feed_update(
            bot,
            _callback_update(
                telegram_id=telegram_id,
                update_id=3,
                callback_id=callback_id,
                message_id=103,
                data="calc_start",
                message_text="🧮 НОВЫЙ РАСЧЁТ",
            ),
        )
        assert await _case_ids(user_id) == case_ids
        assert await _selected_case_id(user_id) == second_case_id

        async with AsyncSessionLocal() as db:
            request_count = await db.scalar(
                select(func.count(CaseCreationRequest.id)).where(
                    CaseCreationRequest.client_id == user_id,
                    CaseCreationRequest.operation_key
                    == f"telegram_callback:{callback_id}",
                )
            )
            assert request_count == 1
    finally:
        await dispatcher.storage.close()
        await bot.session.close()


@pytest.mark.asyncio
async def test_my_case_selector_preserves_context_until_explicit_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram_id = _telegram_id()
    user_id, cases = await _seed_cases(
        telegram_id,
        [
            (CaseStatus.CALCULATOR_STARTED, None),
            (CaseStatus.CALCULATOR_STARTED, None),
        ],
    )
    first_case_id, first_number = cases[0]
    second_case_id, second_number = cases[1]
    assert await _selected_case_id(user_id) == second_case_id

    dispatcher = _runtime_dispatcher(monkeypatch)
    session = RecordingTelegramSession()
    bot = Bot(token="123456789:" + ("B" * 35), session=session)
    try:
        await dispatcher.feed_update(
            bot,
            _message_update(
                telegram_id=telegram_id,
                update_id=10,
                message_id=201,
                text="📁 Моё дело",
            ),
        )
        selector_text = "\n".join(_texts(session))
        assert "У вас несколько активных обращений" in selector_text
        assert first_number in selector_text
        assert second_number in selector_text
        assert await _selected_case_id(user_id) == second_case_id

        session.calls.clear()
        await dispatcher.feed_update(
            bot,
            _callback_update(
                telegram_id=telegram_id,
                update_id=11,
                callback_id=f"runtime-select-{uuid.uuid4().hex}",
                message_id=202,
                data=f"my_case_select:v2:{first_case_id}",
                message_text="📁 МОИ ОБРАЩЕНИЯ",
            ),
        )
        assert await _selected_case_id(user_id) == first_case_id
        assert any(
            f"Выбрано обращение {first_number}" in text
            for text in _texts(session)
        )
    finally:
        await dispatcher.storage.close()
        await bot.session.close()


@pytest.mark.asyncio
async def test_stale_case_bound_payment_callback_cannot_mutate_other_selected_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram_id = _telegram_id()
    user_id, cases = await _seed_cases(
        telegram_id,
        [
            (CaseStatus.M1_WAITING_PAYMENT_30000, RouteCode.M1.value),
            (CaseStatus.CALCULATOR_STARTED, None),
        ],
    )
    stale_case_id, stale_number = cases[0]
    selected_case_id, _ = cases[1]
    assert await _selected_case_id(user_id) == selected_case_id

    dispatcher = _runtime_dispatcher(monkeypatch)
    session = RecordingTelegramSession()
    bot = Bot(token="123456789:" + ("C" * 35), session=session)
    try:
        await dispatcher.feed_update(
            bot,
            _callback_update(
                telegram_id=telegram_id,
                update_id=20,
                callback_id=f"runtime-stale-pay-{uuid.uuid4().hex}",
                message_id=301,
                data=f"pay_start_30000:v2:{stale_case_id}",
                message_text=f"Обращение № {stale_number}",
            ),
        )

        async with AsyncSessionLocal() as db:
            payment_count = await db.scalar(
                select(func.count(Payment.id)).where(
                    Payment.case_id.in_([stale_case_id, selected_case_id])
                )
            )
            stale_case = await db.get(Case, stale_case_id)
            selected_case = await db.get(Case, selected_case_id)
            assert payment_count == 0
            assert stale_case is not None
            assert selected_case is not None
            assert str(stale_case.status) == CaseStatus.M1_WAITING_PAYMENT_30000.value
            assert str(selected_case.status) == CaseStatus.CALCULATOR_STARTED.value

        assert await _selected_case_id(user_id) == selected_case_id
        assert any(
            "сейчас выбрано другое дело" in text and "Действие не выполнено" in text
            for text in _texts(session)
        )
    finally:
        await dispatcher.storage.close()
        await bot.session.close()


@pytest.mark.asyncio
async def test_exact_old_case_message_history_is_read_only_until_case_is_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram_id = _telegram_id()
    user_id, cases = await _seed_cases(
        telegram_id,
        [
            (CaseStatus.CALCULATOR_STARTED, None),
            (CaseStatus.CALCULATOR_STARTED, None),
        ],
    )
    old_case_id, old_case_number = cases[0]
    selected_case_id, _ = cases[1]

    async with AsyncSessionLocal() as db:
        team_message = CaseMessage(
            case_id=old_case_id,
            sender_type="lawyer",
            sender_id=None,
            source_message_id=None,
            text="Ответ юридической команды по первому обращению",
            is_read=False,
        )
        db.add(team_message)
        await db.commit()
        team_message_id = int(team_message.id)

    dispatcher = _runtime_dispatcher(monkeypatch)
    session = RecordingTelegramSession()
    bot = Bot(token="123456789:" + ("D" * 35), session=session)
    try:
        await dispatcher.feed_update(
            bot,
            _callback_update(
                telegram_id=telegram_id,
                update_id=30,
                callback_id=f"runtime-history-{uuid.uuid4().hex}",
                message_id=401,
                data=f"message_history:v2:{old_case_id}:0",
                message_text=f"Старая переписка · {old_case_number}",
            ),
        )

        async with AsyncSessionLocal() as db:
            persisted = await db.get(CaseMessage, team_message_id)
            assert persisted is not None
            assert persisted.is_read is False

        assert await _selected_case_id(user_id) == selected_case_id
        history_text = "\n".join(_texts(session))
        assert old_case_number in history_text
        assert "Ответ юридической команды по первому обращению" in history_text
    finally:
        await dispatcher.storage.close()
        await bot.session.close()
