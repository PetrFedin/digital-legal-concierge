from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramServerError,
)
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import main_menu, reply_main_menu
from app.bot.screens.common import _home_text

logger = logging.getLogger(__name__)
router = Router()


async def _fsm_state_or_error(state: FSMContext) -> tuple[str | None, bool]:
    try:
        return await state.get_state(), False
    except Exception:
        logger.exception("Не удалось прочитать FSM для fallback-навигации.")
        return None, True


async def _callback_notice(
    callback: CallbackQuery,
    text: str,
    *,
    show_alert: bool = False,
) -> None:
    try:
        await callback.answer(text, show_alert=show_alert)
    except TelegramBadRequest:
        pass
    except (TelegramNetworkError, TelegramServerError):
        logger.warning("Не удалось отправить fallback-ответ Telegram callback.")


@router.callback_query()
async def stale_callback(callback: CallbackQuery, db, state: FSMContext):
    current_state, state_error = await _fsm_state_or_error(state)
    if state_error:
        await _callback_notice(
            callback,
            "Не удалось проверить текущее действие. Повторите позже или используйте /menu.",
            show_alert=True,
        )
        return

    if current_state:
        await _callback_notice(
            callback,
            "Эта кнопка относится к другому экрану. Завершите текущий шаг по последней инструкции или используйте /cancel.",
            show_alert=True,
        )
        return

    text, case_exists, primary_action = await _home_text(db, callback)
    await db.commit()
    recovery_text = (
        "Эта кнопка больше не актуальна. Показано текущее состояние.\n\n" + text
    )
    markup = main_menu(case_exists, primary_action=primary_action)
    try:
        await callback.message.edit_text(recovery_text, reply_markup=markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            try:
                await callback.message.answer(recovery_text, reply_markup=markup)
            except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
                logger.warning("Не удалось открыть fallback-главную новым сообщением.")
                await _callback_notice(
                    callback,
                    "Кнопка устарела. Откройте /menu.",
                    show_alert=True,
                )
                return
    except (TelegramNetworkError, TelegramServerError):
        logger.warning("Не удалось открыть fallback-главную.")
        await _callback_notice(
            callback,
            "Кнопка устарела. Откройте /menu.",
            show_alert=True,
        )
        return

    await _callback_notice(callback, "Показано актуальное состояние.")


@router.message()
async def unknown_message(message: Message, db, state: FSMContext):
    current_state, state_error = await _fsm_state_or_error(state)
    if state_error:
        await message.answer(
            "Не удалось проверить текущее действие. Данные не сброшены. Повторите позже или используйте /menu."
        )
        return

    if current_state:
        await message.answer(
            "Этот ввод не подходит к текущему шагу. Данные не сброшены.\n\n"
            "Следуйте последней инструкции бота или используйте /cancel, если хотите явно отменить действие."
        )
        return

    text, case_exists, primary_action = await _home_text(db, message)
    await db.commit()
    await message.answer(
        "Не удалось распознать действие. Нижнее меню обновлено по текущему состоянию.",
        reply_markup=reply_main_menu(case_exists),
    )
    await message.answer(
        text,
        reply_markup=main_menu(case_exists, primary_action=primary_action),
    )
