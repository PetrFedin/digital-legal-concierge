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


async def _render_guard(
    event: CallbackQuery,
    *,
    text: str,
    markup,
    log_context: str,
) -> None:
    try:
        await event.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            try:
                await event.message.answer(text, reply_markup=markup)
            except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
                logger.warning("Could not render %s", log_context, exc_info=True)
    except (TelegramNetworkError, TelegramServerError):
        logger.warning("Telegram unavailable while rendering %s", log_context, exc_info=True)


class ConsentRouteSelectionMiddleware:
    """Prevent an old consent callback from silently selecting M1.

    The consent screen is valid only after the client explicitly selected the
    full-service route and the case reached CLIENT_DECISION. Telegram keeps old
    inline buttons indefinitely, so a ``consent_accept`` callback received while
    the case is still CALCULATED must be treated as stale and routed back to the
    explicit choice instead of mutating the legal workflow.
    """

    async def __call__(self, handler, event, data):
        if not isinstance(event, CallbackQuery) or event.data != "consent_accept":
            return await handler(event, data)

        db = data.get("db")
        if db is None:
            logger.error("Consent route guard did not receive a DB session")
            await _render_guard(
                event,
                text=(
                    "Не удалось безопасно проверить текущий этап. Согласие не применено. "
                    "Откройте «Моё дело» и продолжите с актуального шага."
                ),
                markup=one(
                    ("📁 Моё дело", "my_case_open"),
                    ("🏠 Главная", "nav_home"),
                ),
                log_context="consent route DB guard",
            )
            return None

        try:
            ctx = BotContextService(db)
            user = await ctx.get_user_from_callback(event)
            case = await ctx.case_service.get_active_case_for_user(user.id)
        except Exception:
            logger.exception("Не удалось проверить этап перед подтверждением согласия")
            await db.rollback()
            await _render_guard(
                event,
                text=(
                    "Не удалось безопасно проверить текущий этап. Согласие не применено. "
                    "Откройте «Моё дело» и повторите действие."
                ),
                markup=one(
                    ("📁 Моё дело", "my_case_open"),
                    ("🏠 Главная", "nav_home"),
                ),
                log_context="consent route lookup guard",
            )
            return None

        if case is None or _case_status(case) != CaseStatus.CALCULATED:
            return await handler(event, data)

        await db.rollback()
        await _render_guard(
            event,
            text=(
                "🧭 СНАЧАЛА ВЫБЕРИТЕ ДАЛЬНЕЙШИЙ ПУТЬ\n\n"
                "Предварительный расчёт сохранён, но маршрут юридической помощи ещё не выбран. "
                "Старая кнопка согласия не может сама перевести обращение в полное ведение M1.\n\n"
                "Выберите дальнейший путь. Если выберете ведение дела, согласие на обработку "
                "документов появится отдельным следующим шагом."
            ),
            markup=one(
                ("▶️ Выбрать дальнейший путь", "calc_decision_open"),
                ("💬 Перейти к консультации", "calc_to_m2"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
            log_context="stale consent route selection",
        )
        return None


class ConsultationRouteIsolationMiddleware:
    """Fail closed for stale consultation callbacks that conflict with case state.

    Telegram messages can live for months. A stale M2 callback must not start,
    reserve, pay, cancel, or reschedule a consultation while M1 is active. A case
    in ``ERROR`` is even stricter: no old consultation mutation is allowed on
    either route until staff resolves the technical state. The client receives a
    clear recovery screen with read-only context and messaging instead of being
    sent into another business flow.

    The one intentional M1 exception is ``M1_REJECTED``: policy explicitly lets
    the client choose M2, and a dedicated recovery router handles that decision
    without creating a parallel case. Read-only terminal consultation results
    remain available so an M1 follow-up cannot hide an earlier M2 outcome.
    """

    async def __call__(self, handler, event, data):
        if isinstance(event, CallbackQuery) and event.data == "consent_accept":
            return await ConsentRouteSelectionMiddleware()(handler, event, data)
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
        if case is None:
            return await handler(event, data)

        status = _case_status(case)
        if status == CaseStatus.ERROR:
            text = (
                "🛠 ОБРАЩЕНИЕ НА ТЕХНИЧЕСКОЙ ПРОВЕРКЕ\n\n"
                f"Дело {case.case_number}\n\n"
                "СЕЙЧАС\n"
                "Система не может безопасно определить следующий автоматический этап. "
                "Поэтому старая кнопка не создаёт запись, не резервирует время и не запускает оплату.\n\n"
                "ВАШИ ДАННЫЕ\n"
                "Обращение, документы и история сохранены. Юридическая команда видит дело в отдельной технической очереди.\n\n"
                "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
                "Напишите команде по этому делу. После проверки в «Моём деле» появится актуальное действие."
            )
            markup = one(
                ("✉️ Написать команде", "message_create"),
                ("📁 Проверить моё дело", "my_case_open"),
                ("📄 Документы", "documents_open"),
                ("🕘 История", "case_history_open"),
                ("🏠 Главная", "nav_home"),
            )
            await _render_guard(
                event,
                text=text,
                markup=markup,
                log_context="case ERROR recovery guard",
            )
            return None

        if str(case.route or "") != RouteCode.M1.value:
            return await handler(event, data)

        if event.data == "contact_lawyer" and status == CaseStatus.M1_REJECTED:
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
        await _render_guard(
            event,
            text=text,
            markup=markup,
            log_context="M1 consultation route guard",
        )
        return None