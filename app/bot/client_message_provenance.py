from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.types import CallbackQuery, Message

from app.bot.context import BotContextService
from app.bot.keyboards import one

logger = logging.getLogger(__name__)

_MESSAGE_ENTRY_CALLBACKS = {
    "message_create",
}


async def _current_case(event, db):
    ctx = BotContextService(db)
    if isinstance(event, CallbackQuery):
        user = await ctx.get_user_from_callback(event)
    else:
        user = await ctx.get_user_from_message(event)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    return user, case


async def _recover(event, state, text: str) -> None:
    if state is not None:
        try:
            await state.clear()
        except Exception:
            logger.warning("Не удалось очистить FSM переписки после смены дела")
    markup = one(
        ("✉️ Написать по актуальному делу", "message_create"),
        ("📁 Моё дело", "my_case_open"),
        ("🏠 Главная", "nav_home"),
    )
    try:
        if isinstance(event, CallbackQuery):
            try:
                await event.message.edit_text(text, reply_markup=markup)
            except TelegramBadRequest as error:
                if "message is not modified" not in str(error).lower():
                    await event.message.answer(text, reply_markup=markup)
            try:
                await event.answer("Старый экран не отправил сообщение.")
            except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
                pass
        else:
            await event.answer(text, reply_markup=markup)
    except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
        logger.warning("Telegram не показал recovery переписки")


class ClientMessageProvenanceMiddleware:
    """Prevent a delayed client draft from being attached to another case."""

    async def __call__(self, handler, event, data):
        state = data.get("state")
        db = data.get("db")

        if isinstance(event, CallbackQuery) and str(event.data or "") in _MESSAGE_ENTRY_CALLBACKS:
            result = await handler(event, data)
            if db is None or state is None:
                return result
            try:
                _user, case = await _current_case(event, db)
                if case is not None:
                    await state.update_data(client_message_case_id=int(case.id))
            except Exception:
                logger.exception("Не удалось привязать черновик сообщения к делу")
            return result

        if not isinstance(event, Message) or state is None:
            return await handler(event, data)

        try:
            snapshot = await state.get_data()
        except Exception:
            return await handler(event, data)
        raw_case_id = snapshot.get("client_message_case_id")
        if raw_case_id in (None, ""):
            return await handler(event, data)

        if db is None:
            await _recover(
                event,
                state,
                "Не удалось подтвердить дело для этого сообщения. Текст не отправлен команде.",
            )
            return None

        try:
            expected_case_id = int(raw_case_id or 0)
            _user, case = await _current_case(event, db)
        except Exception:
            logger.exception("Не удалось проверить provenance клиентского сообщения")
            await db.rollback()
            await _recover(
                event,
                state,
                "Не удалось безопасно проверить текущее дело. Текст не отправлен команде.",
            )
            return None

        if expected_case_id <= 0 or case is None or int(case.id) != expected_case_id:
            await db.rollback()
            await _recover(
                event,
                state,
                "ℹ️ Пока вы готовили сообщение, активное дело изменилось. Текст не был записан в другое обращение и остался видимым в вашем чате. Откройте актуальную переписку и отправьте его туда после проверки.",
            )
            return None

        result = await handler(event, data)
        try:
            current_state = await state.get_state()
            if current_state is not None:
                await state.update_data(client_message_case_id=None)
        except Exception:
            logger.warning("Не удалось очистить использованный provenance сообщения")
        return result


__all__ = ["ClientMessageProvenanceMiddleware"]
