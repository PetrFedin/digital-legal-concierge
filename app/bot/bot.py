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
from aiogram.types import BotCommand, BotCommandScopeDefault, CallbackQuery

from app.bot.screens import (
    calculator,
    common,
    consultations,
    documents,
    history,
    m1_stages,
    messages,
    my_case,
    payments,
)
from app.config import settings
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


class DbMiddleware:
    async def __call__(self, handler, event, data):
        async with AsyncSessionLocal() as db:
            data["db"] = db
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
                    # The callback may already have been answered with an alert,
                    # or it may have expired while the handler was running.
                    pass
                except (TelegramNetworkError, TelegramServerError):
                    logger.warning("Не удалось подтвердить callback Telegram.")


async def setup_telegram_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="start", description="Запустить бота"),
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="status", description="Мое дело и текущий статус"),
        BotCommand(command="help", description="Помощь"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())


def build_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.update.middleware(DbMiddleware())
    dispatcher.callback_query.middleware(CallbackAcknowledgeMiddleware())
    for router in [
        common.router,
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
            await bot.delete_webhook(drop_pending_updates=False, request_timeout=30)
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
            logger.warning("Telegram ограничил запросы. Повтор через %s сек.", wait_seconds)
            await asyncio.sleep(wait_seconds)
        except (TelegramNetworkError, TelegramServerError, TimeoutError, OSError) as exc:
            logger.warning(
                "Telegram временно недоступен: %s. Повтор подключения через %s сек.",
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
                "Неожиданная ошибка Telegram-бота. Повтор подключения через %s сек.",
                retry_delay,
            )
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)
        finally:
            await bot.session.close()
