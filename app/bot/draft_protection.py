from __future__ import annotations

import logging

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramServerError,
)

from app.bot.keyboards import one

logger = logging.getLogger(__name__)

MESSAGE_DRAFT = "message"
CONSULTATION_DRAFT = "consultation"

_MESSAGE_DRAFT_CALLBACKS = {
    "message_submit",
    "message_edit_text",
    "message_back_urgency",
    "message_back_category",
    "message_review_return",
    "message_discard_confirm",
    "message_discard",
}
_MESSAGE_DRAFT_CALLBACK_PREFIXES = (
    "msg_cat_",
    "msg_urgency_",
)

_CONSULTATION_DRAFT_CALLBACKS = {
    "consult_subject_start",
    "consult_subject_new",
    "consult_description_review",
    "consult_description_edit",
    "consult_description_confirm",
    "consult_description_discard_confirm",
    "consult_description_discard",
}
_CONSULTATION_DRAFT_CALLBACK_PREFIXES = (
    "consult_subject_case:",
)

_PROTECTED_NAVIGATION_MESSAGES = frozenset(
    {
        "/start",
        "/menu",
        "🏠 Главная",
        "🧮 Рассчитать неустойку",
        "📁 Мое дело",
        "📁 Моё дело",
        "📄 Документы",
        "💬 Переписка",
        "✉️ Новый вопрос",
        "💬 Связаться с юристом",
        "/cancel",
        "Отмена",
    }
)


def protected_draft_kind(state_data: dict | None) -> str | None:
    data = state_data or {}
    if str(data.get("draft_text") or "").strip():
        return MESSAGE_DRAFT
    if str(data.get("description_draft") or "").strip():
        return CONSULTATION_DRAFT
    return None


def is_draft_flow_callback(
    callback_data: str | None,
    draft_kind: str = MESSAGE_DRAFT,
) -> bool:
    value = str(callback_data or "")
    if draft_kind == CONSULTATION_DRAFT:
        return value in _CONSULTATION_DRAFT_CALLBACKS or value.startswith(
            _CONSULTATION_DRAFT_CALLBACK_PREFIXES
        )
    return value in _MESSAGE_DRAFT_CALLBACKS or value.startswith(
        _MESSAGE_DRAFT_CALLBACK_PREFIXES
    )


def is_protected_navigation_message(text: str | None) -> bool:
    return str(text or "").strip() in _PROTECTED_NAVIGATION_MESSAGES


def draft_guard_text(draft_kind: str = MESSAGE_DRAFT) -> str:
    if draft_kind == CONSULTATION_DRAFT:
        return (
            "📝 У вас есть несохранённый черновик вопроса к консультации.\n\n"
            "Я не закрываю его автоматически, чтобы текст не потерялся. "
            "Вернитесь к проверке вопроса или удалите черновик явно — удаление "
            "потребует отдельного подтверждения."
        )
    return (
        "📝 У вас есть неотправленный черновик вопроса.\n\n"
        "Я не закрываю его автоматически, чтобы введённый текст не потерялся. "
        "Вернитесь к черновику или отмените его явно — удаление потребует подтверждения."
    )


def draft_guard_markup(draft_kind: str = MESSAGE_DRAFT):
    if draft_kind == CONSULTATION_DRAFT:
        return one(
            ("↩️ Вернуться к вопросу", "consult_description_review"),
            ("✖️ Удалить черновик", "consult_description_discard_confirm"),
        )
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


async def _show_callback_guard(event, draft_kind: str) -> None:
    message = getattr(event, "message", None)
    if message is None:
        await _acknowledge(
            event,
            "Сначала сохраните или явно удалите текущий черновик.",
            show_alert=True,
        )
        return

    text = draft_guard_text(draft_kind)
    markup = draft_guard_markup(draft_kind)
    try:
        await message.edit_text(
            text,
            reply_markup=markup,
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            try:
                await message.answer(
                    text,
                    reply_markup=markup,
                )
            except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
                logger.warning("Не удалось показать экран защиты черновика.")
    except (TelegramNetworkError, TelegramServerError):
        logger.warning("Не удалось показать экран защиты черновика.")

    await _acknowledge(event, "Черновик сохранён.")


async def _show_message_guard(event, draft_kind: str) -> None:
    try:
        await event.answer(
            draft_guard_text(draft_kind),
            reply_markup=draft_guard_markup(draft_kind),
        )
    except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
        logger.warning("Не удалось показать защиту черновика для нижнего меню.")


class DraftProtectionMiddleware:
    """Block stale inline navigation from silently deleting an unsaved client draft."""

    async def __call__(self, handler, event, data):
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

        draft_kind = protected_draft_kind(state_data)
        if draft_kind is None:
            return await handler(event, data)

        if is_draft_flow_callback(getattr(event, "data", None), draft_kind):
            return await handler(event, data)

        await _show_callback_guard(event, draft_kind)
        return None


class DraftMessageNavigationProtectionMiddleware:
    """Protect drafts when a persistent reply-menu navigation command is pressed."""

    async def __call__(self, handler, event, data):
        if not is_protected_navigation_message(getattr(event, "text", None)):
            return await handler(event, data)

        state = data.get("state")
        if state is None or not hasattr(state, "get_data"):
            return await handler(event, data)

        try:
            state_data = await state.get_data()
        except Exception:
            logger.exception("Не удалось проверить FSM перед reply-навигацией.")
            try:
                await event.answer(
                    "Не удалось проверить сохранённый черновик. Повторите действие позже."
                )
            except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
                logger.warning("Не удалось показать ошибку проверки FSM.")
            return None

        draft_kind = protected_draft_kind(state_data)
        if draft_kind is None:
            return await handler(event, data)

        await _show_message_guard(event, draft_kind)
        return None
