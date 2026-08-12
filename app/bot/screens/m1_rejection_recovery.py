from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.filters import Filter
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.consultations.consultation_intake import (
    ActiveCaseRouteConflict,
    ConsultationIntakeService,
)
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


class RejectedM1ContactFilter(Filter):
    """Turn the generic contact button into the actual M1 rejection decision."""

    async def __call__(self, callback: CallbackQuery, db) -> bool:
        if callback.data != "contact_lawyer":
            return False
        _ctx, _user, case = await _active(callback, db)
        return _status(case) == CaseStatus.M1_REJECTED


async def _show_options(callback: CallbackQuery, case) -> None:
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
            ("👨‍⚖ Перейти в консультацию", "m1_rejected_to_m2"),
            ("✉️ Уточнить решение у команды", "message_create"),
            ("Завершить обращение", "m1_rejected_close"),
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


@router.callback_query(lambda c: c.data == "m1_rejected_to_m2")
async def rejected_m1_to_consultation(callback: CallbackQuery, db):
    ctx, user, case = await _active(callback, db)
    if not case:
        await _safe_edit(
            callback,
            "Активное дело уже завершено. Перевод в консультацию не выполнялся.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    if _status(case) != CaseStatus.M1_REJECTED:
        await _safe_edit(
            callback,
            "Эта кнопка относится к старому решению. Дело не изменено — показан безопасный возврат.",
            reply_markup=one(
                ("📁 Открыть актуальное дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    try:
        await ctx.case_service.transfer_to_m2(
            case=case,
            actor_type="client",
            actor_id=user.id,
            reason="Клиент выбрал консультацию после отказа в полном ведении M1",
        )
        _case, consultation = await ConsultationIntakeService(db).get_or_create_context(user)
        await db.commit()
    except ActiveCaseRouteConflict as error:
        await db.rollback()
        await _safe_edit(
            callback,
            f"Перевод не выполнен: {error}\n\nТекущее дело сохранено без изменений.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except (ValueError, LookupError) as error:
        await db.rollback()
        await _safe_edit(
            callback,
            f"Перевод в консультацию пока не завершён: {error}\n\nПовторите из актуального дела или напишите команде.",
            reply_markup=one(
                ("🔄 Проверить дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось перевести отклонённый M1 в M2")
        await _safe_edit(
            callback,
            "Перевод в консультацию временно не завершён. Данные не потеряны; откройте актуальное дело перед повтором.",
            reply_markup=one(
                ("🔄 Открыть актуальное дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    description_ready = bool(str(consultation.client_description or "").strip())
    primary = (
        ("📅 Продолжить: выбрать время", "consult_booking_start")
        if description_ready
        else ("📝 Описать вопрос", "consult_subject_start")
    )
    await _safe_edit(
        callback,
        "✅ Обращение переведено в консультацию.\n\n"
        "Документы и история остаются в том же деле. Отдельное дублирующее обращение не создано.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Опишите вопрос для консультации или продолжите с уже сохранённым описанием.",
        reply_markup=one(
            primary,
            ("📄 Документы", "documents_open"),
            ("✉️ Написать команде", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "m1_rejected_close")
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
    await _safe_edit(
        callback,
        "⚠️ Завершить обращение?\n\n"
        "После подтверждения дело станет закрытым и перейдёт в режим просмотра. Документы, переписка и история сохранятся в архиве.\n\n"
        "Если вы хотите сначала уточнить решение или перейти в консультацию, вернитесь назад.",
        reply_markup=one(
            ("✅ Да, завершить обращение", "m1_rejected_close_confirm"),
            ("← Вернуться к вариантам", "contact_lawyer"),
            ("✉️ Написать команде", "message_create"),
        ),
    )


@router.callback_query(lambda c: c.data == "m1_rejected_close_confirm")
async def rejected_m1_close_confirm(callback: CallbackQuery, db):
    ctx, user, case = await _active(callback, db)
    if not case:
        await _safe_edit(
            callback,
            "Обращение уже не активно. Повторное закрытие не выполнялось.",
            reply_markup=one(
                ("📁 Открыть архив", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    if _status(case) != CaseStatus.M1_REJECTED:
        await _safe_edit(
            callback,
            "Статус дела уже изменился. Повторное закрытие не выполнялось.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    try:
        await ctx.case_service.change_status(
            case=case,
            next_status=CaseStatus.M1_CLOSED,
            actor_type="client",
            actor_id=user.id,
            comment="Клиент завершил обращение после отказа в полном ведении M1",
        )
        await db.commit()
    except ValueError as error:
        await db.rollback()
        await _safe_edit(
            callback,
            f"Закрытие не выполнено: {error}\n\nОткройте актуальное состояние дела.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось закрыть отклонённый M1")
        await _safe_edit(
            callback,
            "Обращение временно не закрыто. Ничего не потеряно; повторите из актуального дела.",
            reply_markup=one(
                ("🔄 Открыть дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _safe_edit(
        callback,
        "✅ Обращение завершено.\n\n"
        "Оно больше не считается активным. Документы, переписка и история сохранены в архиве. Новое обращение можно начать отдельно.",
        reply_markup=one(
            ("📁 Открыть архив обращения", "my_case_open"),
            ("🧮 Новое обращение", "calc_start"),
            ("🏠 Главная", "nav_home"),
        ),
    )
