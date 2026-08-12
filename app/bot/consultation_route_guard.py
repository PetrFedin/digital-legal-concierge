from __future__ import annotations

import logging

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramServerError,
)
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.statuses.case_statuses import CaseStatus, RouteCode

logger = logging.getLogger(__name__)

CONSULTATION_CALLBACK_PREFIXES = (
    "consult_",
    "consultation_",
)
CONSULTATION_ENTRY_CALLBACKS = frozenset(
    {
        "contact_lawyer",
    }
)
CONSULTATION_READ_ONLY_CALLBACKS = frozenset(
    {
        "consultation_result_open",
    }
)


def is_consultation_callback(data: str | None) -> bool:
    value = str(data or "")
    if value in CONSULTATION_READ_ONLY_CALLBACKS:
        return False
    return value in CONSULTATION_ENTRY_CALLBACKS or any(
        value.startswith(prefix) for prefix in CONSULTATION_CALLBACK_PREFIXES
    )


def _case_status(case) -> CaseStatus | None:
    try:
        return case.status if isinstance(case.status, CaseStatus) else CaseStatus(str(case.status))
    except (TypeError, ValueError):
        return None


class ConsultationRouteIsolationMiddleware:
    """Fail closed when an old M2 callback is pressed during an active M1 case.

    Telegram messages can live for months. A stale consultation button must not
    start, reserve, pay, cancel, or reschedule an M2 flow while M1 is active.
    The generic ``contact_lawyer`` entry is guarded too because legacy Telegram
    screens may still expose it and older router ownership must never bypass the
    active-case route. The one intentional exception is M1_REJECTED: policy
    explicitly allows the client to choose M2, and a dedicated recovery router
    handles that decision without creating a parallel case. Read-only terminal
    consultation results remain available so an M1 follow-up cannot hide the
    outcome of a consultation that was already completed.

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

        if (
            event.data == "contact_lawyer"
            and _case_status(case) == CaseStatus.M1_REJECTED
        ):
            # This is not a stale M2 button: it is the explicit decision screen
            # after M1 rejection. The recovery router still requires a second
            # confirmed action before changing route or closing the case.
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