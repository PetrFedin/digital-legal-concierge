from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.cases.consent_decision_service import (
    CONSENT_ACCEPT,
    CONSENT_DECLINE,
    ConsentDecisionError,
    ConsentDecisionService,
)

router = Router()
logger = logging.getLogger(__name__)


async def _safe_edit(callback: CallbackQuery, text: str, *, reply_markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
    except (TelegramNetworkError, TelegramServerError):
        logger.warning("Telegram не обновил атомарный экран согласия")


async def _stale(callback: CallbackQuery, db, outcome: str) -> None:
    await db.rollback()
    if outcome == "stale_m1":
        text = (
            "Согласие уже было учтено, а ведение дела перешло дальше. "
            "Старая кнопка ничего не изменила."
        )
        buttons = (
            ("📁 Открыть текущее дело", "my_case_open"),
            ("📄 Документы", "documents_open"),
            ("✉️ Написать команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        )
    elif outcome == "stale_m2":
        text = (
            "Эта кнопка согласия относится к прежнему маршруту. Сейчас активно "
            "консультационное обращение; статус и документы не изменены."
        )
        buttons = (
            ("📁 Открыть текущее дело", "my_case_open"),
            ("✉️ Написать команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        )
    else:
        text = (
            "Эта кнопка больше не соответствует текущему этапу. Ничего не изменено — "
            "откройте актуальную карточку дела."
        )
        buttons = (
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    await _safe_edit(callback, text, reply_markup=one(*buttons))


@router.callback_query(lambda c: c.data == "consent_accept")
async def guarded_consent_accept(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        result = await ConsentDecisionService(db).apply(
            client_id=user.id,
            decision=CONSENT_ACCEPT,
        )
        if result.outcome == "route_not_selected":
            await db.rollback()
            await _safe_edit(
                callback,
                "Сначала выберите дальнейший путь после расчёта. Старая кнопка согласия не выбирает M1 автоматически и ничего не изменила.",
                reply_markup=one(
                    ("🧭 Выбрать дальнейший путь", "calc_decision_open"),
                    ("📁 Моё дело", "my_case_open"),
                    ("🏠 Главная", "nav_home"),
                ),
            )
            return
        if result.outcome != "accepted":
            await _stale(callback, db, result.outcome)
            return
        await db.commit()
    except LookupError:
        await db.rollback()
        await _safe_edit(
            callback,
            "Согласие не сохранено: активное дело больше не найдено. Старое сообщение не создаёт новое обращение.",
            reply_markup=one(
                ("🧮 Новый расчёт", "calc_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except (ConsentDecisionError, ValueError) as error:
        await db.rollback()
        await _safe_edit(
            callback,
            f"Согласие не сохранено: {error}",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Атомарное сохранение согласия клиента не завершено")
        await _safe_edit(
            callback,
            "Согласие временно не сохранено. Переход в M1 отменён целиком; повторите действие из актуального экрана.",
            reply_markup=one(
                ("🔄 Открыть согласие", "consent_open"),
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _safe_edit(
        callback,
        "✅ Согласие сохранено.\n\n"
        "Маршрут ведения дела теперь начат. Следующий шаг — загрузить документы и передать их юридической команде.",
        reply_markup=one(
            ("📄 Перейти к документам", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "consent_decline_confirm")
async def guarded_consent_decline(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        result = await ConsentDecisionService(db).apply(
            client_id=user.id,
            decision=CONSENT_DECLINE,
        )
        if result.outcome in {"stale_m1", "stale_m2", "stale_other"}:
            await _stale(callback, db, result.outcome)
            return
        await db.commit()
    except LookupError:
        await db.rollback()
        await _safe_edit(
            callback,
            "Активное дело больше не найдено. Старая кнопка отказа ничего не изменила.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return
    except (ConsentDecisionError, ValueError) as error:
        await db.rollback()
        await _safe_edit(
            callback,
            f"Отказ не сохранён: {error}",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Атомарное сохранение отказа от согласия не завершено")
        await _safe_edit(
            callback,
            "Отказ временно не сохранён. Текущий этап не изменён; откройте актуальное дело перед повтором.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _safe_edit(
        callback,
        "Согласие не предоставлено.\n\n"
        "Ведение дела не начато, документы юристу не передаются. Предварительный расчёт сохранён, и выбор можно сделать позже.",
        reply_markup=one(
            ("🧭 Вернуться к выбору пути", "calc_decision_open"),
            ("💬 Перейти к консультации", "calc_to_m2"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


__all__ = ["router"]
