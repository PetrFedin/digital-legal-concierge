import asyncio
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.bot.screens import common, calculator, my_case, documents, payments, consultations, m1_stages, messages, history


class DbMiddleware:
    async def __call__(self, handler, event, data):
        async with AsyncSessionLocal() as db:
            data['db'] = db
            return await handler(event, data)


async def setup_telegram_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command='start', description='Запустить бота'),
        BotCommand(command='menu', description='Главное меню'),
        BotCommand(command='status', description='Мое дело и текущий статус'),
        BotCommand(command='help', description='Помощь'),
        BotCommand(command='cancel', description='Отменить текущее действие'),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())


async def run_bot():
    if not settings.bot_token or settings.bot_token == 'CHANGE_ME':
        print('BOT_TOKEN не задан. Бот не запущен.')
        return

    bot = Bot(token=settings.bot_token)
    # Polling cannot work while an old webhook is active. Remove it safely on startup.
    await bot.delete_webhook(drop_pending_updates=False)
    await setup_telegram_commands(bot)

    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(DbMiddleware())

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
        dp.include_router(router)

    print('Telegram-бот запущен в polling-режиме.')
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
