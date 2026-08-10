from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.statuses.case_statuses import RouteCode

logger = logging.getLogger(__name__)

CONSULTATION_CALLBACK_PREFIXES = (
    "consult_",
    "consultation_",
)


def is_consultation_callback(data: str | None) -> bool:
    value = str(data or "")
    return any(value.startswith(prefix) for prefix in CONSULTATION_CALLBACK_PREFIXES)


class ConsultationRouteIsolationMiddleware:
    """Fail closed when an old M2 callback is pressed during an active M1 case.

    Telegram messages can live for months. A stale consultation button must not
    start, reserve, pay, cancel, or reschedule an M2 flow while M1 is active.
    Pre-route cases and active M2 cases are intentionally passed through to the
    domain handlers, which keep their own status/snapshot checks.
    """

    async def __call__(self, handler, event, data):
        if not isinstance(event, CallbackQuery) or not is_consultation_callback(event.data):
            return await handler(event, data)

        db = data.get("db")
        if db is None:
            # DbMiddleware is expected to run before callback middlewares. Do not
            # invent state if the dispatcher contract is broken.
            logger.error("Consultation route guard did not receive a DB session")
            return await handler(event, data)

        ctx = BotContextService(db)
        user = await ctx.get_user_from_callback(event)
        case = await ctx.case_service.get_active_case_for_user(user.id)
        if case is None or str(case.route or "") != RouteCode.M1.value:
            return await handler(event, data)

        text = (
            "🔒 Эта кнопка относится к маршруту консультации, но сейчас у вас активно M1-дело.\n\n"
            f"Дело {case.case_number} не изменено. Старая кнопка не создаёт консультацию, "
            "не резервирует время и не запускает оплату. Продолжите текущее дело или "
            "напишите команде в его контексте."
        )
        markup = one(
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Написать по текущему делу", "message_create"),
            ("📄 Документы", "documents_open"),
            ("🏠 Главная", "nav_home"),
        )
        try:
            await event.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).lower():
                try:
                    await event.message.answer(text, reply_markup=markup)
                except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
                    logger.warning("Could not render M1 consultation route guard", exc_info=True)
        except (TelegramNetworkError, TelegramServerError):
            logger.warning("Telegram unavailable while rendering M1 route guard", exc_info=True)
        return None
