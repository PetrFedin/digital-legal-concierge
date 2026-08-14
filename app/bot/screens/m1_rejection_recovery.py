from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.filters import Filter
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.statuses.case_statuses import CaseStatus

router = Router()
logger = logging.getLogger(__name__)


def _status(case) -> CaseStatus | None:
    if case is None:
        return None
    try:
        return case.status if isinstance(case.status, CaseStatus) else CaseStatus(str(case.status))
    except (TypeError, ValueError):
        return None


async def _safe_edit(callback: CallbackQuery, text: str, *, reply_markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
        return
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            try:
                await callback.answer("Экран уже актуален.")
            except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
                pass
            return
        logger.warning("Не удалось обновить экран после отказа M1: %s", error)
    except (TelegramNetworkError, TelegramServerError) as error:
        logger.warning("Telegram временно не обновил экран после отказа M1: %s", error)

    try:
        await callback.message.answer(text, reply_markup=reply_markup)
    except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
        logger.exception("Не удалось показать recovery-экран после отказа M1")


async def _active(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    return ctx, user, case


def _bound(action: str, case_id: int) -> str:
    return f"{action}:v2:{int(case_id)}"


def _bound_case_id(callback: CallbackQuery, action: str) -> int | None:
    value = str(callback.data or "")
    prefix = f"{action}:v2:"
    if not value.startswith(prefix):
        return None
    try:
        case_id = int(value[len(prefix) :])
    except ValueError:
        return None
    return case_id if case_id > 0 else None


class RejectedM1ContactFilter(Filter):
    """Turn the generic contact button into the actual M1 rejection decision."""

    async def __call__(self, callback: CallbackQuery, db) -> bool:
        if callback.data != "contact_lawyer":
            return False
        _ctx, _user, case = await _active(callback, db)
        return _status(case) == CaseStatus.M1_REJECTED


async def _show_options(callback: CallbackQuery, case) -> None:
    case_id = int(case.id)
    await _safe_edit(
        callback,
        "⚖️ РЕШЕНИЕ ПО ВЕДЕНИЮ ДЕЛА\n\n"
        f"Дело № {case.case_number}\n\n"
        "СЕЙЧАС\n"
        "Юрист не принял обращение в полное ведение M1. Это не должно оставлять вас без следующего шага.\n\n"
        "ЧТО МОЖНО СДЕЛАТЬ\n"
        "1. Перейти в консультацию — вопрос и текущие материалы останутся в этом обращении.\n"
        "2. Завершить обращение — оно перейдёт в архив и больше не будет активным.\n"
        "3. Сначала написать команде и уточнить решение.\n\n"
        "Выберите один вариант. Ничего не изменится без отдельного подтверждения.",
        reply_markup=one(
            ("👨‍⚖ Перейти в консультацию", _bound("m1_rejected_to_m2", case_id)),
            ("✉️ Уточнить решение у команды", "message_create"),
            ("Завершить обращение", _bound("m1_rejected_close", case_id)),
            ("📄 Документы", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(RejectedM1ContactFilter())
async def rejected_m1_options(callback: CallbackQuery, db):
    _ctx, _user, case = await _active(callback, db)
    if _status(case) != CaseStatus.M1_REJECTED:
        await _safe_edit(
            callback,
            "Решение по делу уже изменилось. Откройте актуальное состояние.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await _show_options(callback, case)


@router.callback_query(
    lambda c: c.data == "m1_rejected_close"
    or (bool(c.data) and c.data.startswith("m1_rejected_close:v2:"))
)
async def rejected_m1_close_prompt(callback: CallbackQuery, db):
    _ctx, _user, case = await _active(callback, db)
    if _status(case) != CaseStatus.M1_REJECTED:
        await _safe_edit(
            callback,
            "Эта кнопка закрытия больше не соответствует текущему этапу. Ничего не изменено.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    expected_case_id = _bound_case_id(callback, "m1_rejected_close")
    if expected_case_id is not None and expected_case_id != int(case.id):
        await _safe_edit(
            callback,
            "Эта кнопка относится к другому обращению. Ничего не изменено. Откройте актуальное дело.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _safe_edit(
        callback,
        "⚠️ Завершить обращение?\n\n"
        f"Дело № {case.case_number}\n\n"
        "После подтверждения именно это дело станет закрытым и перейдёт в режим просмотра. "
        "Документы, переписка и история сохранятся в архиве.\n\n"
        "Если вы хотите сначала уточнить решение или перейти в консультацию, вернитесь назад.",
        reply_markup=one(
            (
                "✅ Да, завершить обращение",
                _bound("m1_rejected_close_confirm", int(case.id)),
            ),
            ("← Вернуться к вариантам", "contact_lawyer"),
            ("✉️ Написать команде", "message_create"),
        ),
    )


__all__ = ["router", "RejectedM1ContactFilter"]
