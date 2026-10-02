from __future__ import annotations

import logging

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramServerError,
)

from app.bot.keyboards import one

logger = logging.getLogger(__name__)

_DRAFT_CALLBACKS = {
    "message_submit",
    "message_edit_text",
    "message_back_urgency",
    "message_back_category",
    "message_review_return",
    "message_discard_confirm",
    "message_discard",
}
_DRAFT_CALLBACK_PREFIXES = (
    "msg_cat_",
    "msg_urgency_",
)


def is_draft_flow_callback(callback_data: str | None) -> bool:
    value = str(callback_data or "")
    return value in _DRAFT_CALLBACKS or value.startswith(_DRAFT_CALLBACK_PREFIXES)


def draft_guard_text() -> str:
    return (
        "📝 У вас есть неотправленный черновик вопроса.\n\n"
        "Я не закрываю его автоматически, чтобы введённый текст не потерялся. "
        "Вернитесь к черновику или отмените его явно — удаление потребует подтверждения."
    )


def draft_guard_markup():
    return one(
        ("↩️ Вернуться к черновику", "message_review_return"),
        ("✖️ Отменить черновик", "message_discard_confirm"),
    )


async def _acknowledge(event, text: str, *, show_alert: bool = False) -> None:
    try:
        await event.answer(text, show_alert=show_alert)
    except TelegramBadRequest:
        pass
    except (TelegramNetworkError, TelegramServerError):
        logger.warning("Не удалось подтвердить защиту черновика в Telegram.")


async def _show_guard(event) -> None:
    message = getattr(event, "message", None)
    if message is None:
        await _acknowledge(
            event,
            "Сначала отправьте или отмените сохранённый черновик вопроса.",
            show_alert=True,
        )
        return

    try:
        await message.edit_text(
            draft_guard_text(),
            reply_markup=draft_guard_markup(),
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            try:
                await message.answer(
                    draft_guard_text(),
                    reply_markup=draft_guard_markup(),
                )
            except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
                logger.warning("Не удалось показать экран защиты черновика.")
    except (TelegramNetworkError, TelegramServerError):
        logger.warning("Не удалось показать экран защиты черновика.")

    await _acknowledge(event, "Черновик сохранён.")


class DraftProtectionMiddleware:
    """Block stale inline navigation from silently deleting a saved legal-question draft."""

    async def __call__(self, handler, event, data):
        if is_draft_flow_callback(getattr(event, "data", None)):
            return await handler(event, data)

        state = data.get("state")
        if state is None or not hasattr(state, "get_data"):
            return await handler(event, data)

        try:
            state_data = await state.get_data()
        except Exception:
            logger.exception("Не удалось проверить FSM перед callback-навигацией.")
            await _acknowledge(
                event,
                "Не удалось проверить сохранённый черновик. Повторите действие позже.",
                show_alert=True,
            )
            return None

        if not str(state_data.get("draft_text") or "").strip():
            return await handler(event, data)

        await _show_guard(event)
        return None
