import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault, CallbackQuery, Message

from app.bot.screens import (
    calculator,
    common,
    consultation_entry,
    consultations,
    documents,
    history,
    m1_stages,
    messages,
    my_case,
    payments,
)
from app.bot.security import SlidingWindowRateLimiter
from app.config import settings
from app.db.session import AsyncSessionLocal
from app.domain.analytics.activity_service import (
    ActivityService,
    BOT_ACTION,
    BOT_COMMAND,
    BOT_DOCUMENT_ACTION,
    BOT_FREE_TEXT,
    BOT_NAVIGATION,
    BOT_PAYMENT_ACTION,
    BOT_SCREEN_VIEW,
)
from app.domain.cases.case_service import CaseService
from app.domain.users.user_service import UserService

logger = logging.getLogger(__name__)


class DbMiddleware:
    async def __call__(self, handler, event, data):
        async with AsyncSessionLocal() as db:
            data["db"] = db
            return await handler(event, data)


class FloodControlMiddleware:
    """Limit rapid Telegram actions from one user without blocking normal use."""

    def __init__(self, *, max_events: int = 8, window_seconds: int = 10):
        self.limiter = SlidingWindowRateLimiter(
            max_events=max_events,
            window_seconds=window_seconds,
        )

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if not user:
            return await handler(event, data)
        decision = self.limiter.check(user.id)
        if decision.allowed:
            return await handler(event, data)

        text = (
            "Слишком много действий подряд. "
            f"Подождите {decision.retry_after_seconds} сек. и повторите."
        )
        try:
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(text)
        except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
            logger.warning("Не удалось отправить уведомление об ограничении частоты.")
        return None


MENU_TEXT_TARGETS = {
    "🏠 Главная": "home",
    "🧮 Рассчитать неустойку": "calculator",
    "📁 Мое дело": "my_case",
    "📁 Моё дело": "my_case",
    "📄 Документы": "documents",
    "💬 Связаться с юристом": "contact_lawyer",
}


def _callback_screen(callback_data: str) -> str:
    value = callback_data.lower()
    if value.startswith(("pay", "payment", "m1_pay", "consult_pay")):
        return "payments"
    if value.startswith(("doc", "documents", "m2_documents")):
        return "documents"
    if value.startswith(("consult", "m2_", "calc_to_m2")):
        return "consultation"
    if value.startswith(("calc", "consent")):
        return "calculator"
    if value.startswith(("case", "my_case", "court", "contract", "poa")):
        return "case"
    if value.startswith(("contact", "message")):
        return "messages"
    if value.startswith("nav_"):
        return "navigation"
    return value.split(":", 1)[0][:80] or "unknown"


def _callback_action(callback_data: str) -> str:
    """Return analytics-safe action without database references or payloads."""
    parts = [part for part in callback_data.split(":") if part]
    if not parts:
        return "unknown"
    if parts[0] == "consult_select" and len(parts) >= 2:
        return ":".join(parts[:2])[:100]
    return parts[0][:100]


def _message_analytics(message: Message) -> tuple[str, dict]:
    text = message.text or message.caption or ""
    stripped = text.strip()
    base = {
        "chat_type": message.chat.type,
        "language_code": getattr(message.from_user, "language_code", None),
        "is_premium": bool(getattr(message.from_user, "is_premium", False)),
        "message_type": "text",
    }

    if stripped.startswith("/"):
        command = stripped.split(maxsplit=1)[0].split("@", 1)[0][:100]
        return BOT_COMMAND, {**base, "command": command, "screen": "command"}

    if stripped in MENU_TEXT_TARGETS:
        target = MENU_TEXT_TARGETS[stripped]
        return BOT_NAVIGATION, {
            **base,
            "target": target,
            "screen": target,
            "navigation_type": "reply_menu",
        }

    media_types = []
    if message.document:
        media_types.append("document")
    if message.photo:
        media_types.append("photo")
    if message.video:
        media_types.append("video")
    if message.voice:
        media_types.append("voice")
    if message.audio:
        media_types.append("audio")
    if message.contact:
        media_types.append("contact")
    if message.location:
        media_types.append("location")

    if media_types:
        return BOT_DOCUMENT_ACTION, {
            **base,
            "message_type": "media",
            "media_types": media_types,
            "has_caption": bool(message.caption),
            "caption_length": len(message.caption or ""),
            "screen": "upload",
        }

    return BOT_FREE_TEXT, {
        **base,
        "text_length": len(stripped),
        "has_text": bool(stripped),
        "screen": "free_text",
    }


def _callback_analytics(callback: CallbackQuery) -> tuple[str, dict]:
    callback_data = str(callback.data or "")
    screen = _callback_screen(callback_data)
    lowered = callback_data.lower()
    if lowered.startswith(("pay", "payment", "m1_pay", "consult_pay")):
        event_name = BOT_PAYMENT_ACTION
    elif lowered.startswith(("doc", "documents", "m2_documents")):
        event_name = BOT_DOCUMENT_ACTION
    elif lowered.endswith("_open") or lowered.startswith(("nav_", "my_case")):
        event_name = BOT_SCREEN_VIEW
    elif lowered.startswith(("calc", "consult", "m1_", "m2_", "case_")):
        event_name = BOT_ACTION
    else:
        event_name = BOT_NAVIGATION
    return event_name, {
        "callback_action": _callback_action(callback_data),
        "callback_length": len(callback_data),
        "screen": screen,
        "navigation_type": "inline_button",
        "message_id": callback.message.message_id if callback.message else None,
        "chat_type": callback.message.chat.type if callback.message else None,
        "language_code": getattr(callback.from_user, "language_code", None),
        "is_premium": bool(getattr(callback.from_user, "is_premium", False)),
    }


class BotActivityMiddleware:
    """Persist privacy-aware visits, views and actions for CRM analytics."""

    async def __call__(self, handler, event, data):
        db = data.get("db")
        telegram_user = getattr(event, "from_user", None)
        if db is None or telegram_user is None:
            return await handler(event, data)

        try:
            user = await UserService(db).get_or_create_from_telegram(
                telegram_id=telegram_user.id,
                telegram_username=telegram_user.username,
                full_name=telegram_user.full_name,
            )
            case = await CaseService(db).get_active_case_for_user(user.id)
            if isinstance(event, CallbackQuery):
                event_name, payload = _callback_analytics(event)
            elif isinstance(event, Message):
                event_name, payload = _message_analytics(event)
            else:
                event_name, payload = BOT_ACTION, {
                    "update_type": type(event).__name__
                }

            await ActivityService(db).record_bot_interaction(
                event_name=event_name,
                user_id=user.id,
                case_id=case.id if case else None,
                route=case.route if case else None,
                payload=payload,
            )
            await db.commit()
            data["crm_user"] = user
            data["crm_case"] = case
        except Exception:
            await db.rollback()
            logger.exception("Не удалось записать CRM-аналитику Telegram-события.")

        return await handler(event, data)


class CallbackAcknowledgeMiddleware:
    """Always close Telegram's loading spinner after callback processing."""

    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        finally:
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer()
                except TelegramBadRequest:
                    pass
                except (TelegramNetworkError, TelegramServerError):
                    logger.warning("Не удалось подтвердить callback Telegram.")


async def setup_telegram_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="start", description="Запустить бота"),
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="status", description="Моё дело и текущий статус"),
        BotCommand(command="help", description="Помощь"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())


def build_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.update.middleware(DbMiddleware())
    flood_control = FloodControlMiddleware()
    activity = BotActivityMiddleware()
    dispatcher.message.middleware(flood_control)
    dispatcher.message.middleware(activity)
    dispatcher.callback_query.middleware(flood_control)
    dispatcher.callback_query.middleware(activity)
    dispatcher.callback_query.middleware(CallbackAcknowledgeMiddleware())
    for router in [
        common.router,
        consultation_entry.router,
        calculator.router,
        my_case.router,
        documents.router,
        payments.router,
        consultations.router,
        m1_stages.router,
        messages.router,
        history.router,
    ]:
        dispatcher.include_router(router)
    return dispatcher


async def run_bot() -> None:
    if not settings.bot_token or settings.bot_token == "CHANGE_ME":
        logger.error("BOT_TOKEN не задан. Telegram-бот не запущен.")
        return

    retry_delay = 5
    max_retry_delay = 60
    dispatcher = build_dispatcher()

    while True:
        bot = Bot(token=settings.bot_token)
        try:
            logger.info("Подключение к Telegram API...")
            await bot.delete_webhook(
                drop_pending_updates=False,
                request_timeout=30,
            )
            await setup_telegram_commands(bot)
            logger.info("Telegram-бот запущен в polling-режиме.")
            retry_delay = 5
            await dispatcher.start_polling(
                bot,
                allowed_updates=dispatcher.resolve_used_update_types(),
                polling_timeout=30,
                handle_signals=False,
                close_bot_session=False,
            )
        except TelegramRetryAfter as exc:
            wait_seconds = max(int(exc.retry_after), retry_delay)
            logger.warning(
                "Telegram ограничил запросы. Повтор через %s сек.",
                wait_seconds,
            )
            await asyncio.sleep(wait_seconds)
        except (
            TelegramNetworkError,
            TelegramServerError,
            TimeoutError,
            OSError,
        ) as exc:
            logger.warning(
                "Telegram временно недоступен: %s. "
                "Повтор подключения через %s сек.",
                exc,
                retry_delay,
            )
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)
        except asyncio.CancelledError:
            logger.info("Остановка Telegram-бота.")
            raise
        except Exception:
            logger.exception(
                "Неожиданная ошибка Telegram-бота. "
                "Повтор подключения через %s сек.",
                retry_delay,
            )
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)
        finally:
            await bot.session.close()
