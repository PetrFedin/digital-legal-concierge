from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.cases.m1_rejection_decision_service import (
    DECISION_CLOSE,
    DECISION_TO_M2,
    M1RejectionDecisionError,
    M1RejectionDecisionService,
)
from app.domain.consultations.consultation_intake import (
    ActiveCaseRouteConflict,
    ConsultationIntakeService,
)

router = Router()
logger = logging.getLogger(__name__)


async def _safe_edit(callback: CallbackQuery, text: str, *, reply_markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            raise
    except (TelegramNetworkError, TelegramServerError):
        logger.warning("Telegram не обновил экран решения после отказа M1")


async def _stale(callback: CallbackQuery, db, text: str) -> None:
    await db.rollback()
    await _safe_edit(
        callback,
        text,
        reply_markup=one(
            ("📁 Открыть актуальное дело", "my_case_open"),
            ("✉️ Написать команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "m1_rejected_to_m2")
async def guarded_rejected_m1_to_m2(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        result = await M1RejectionDecisionService(db).apply(
            client_id=user.id,
            decision=DECISION_TO_M2,
        )
        if result.outcome != "m2_intake":
            await _stale(
                callback,
                db,
                "Эта кнопка относится к уже изменившемуся решению. Ничего не изменено — показан безопасный возврат к текущему делу.",
            )
            return

        # Keep the route switch and consultation record atomic. A failed intake
        # rolls the route switch back, so no M2 case is left without its context.
        _case, consultation = await ConsultationIntakeService(db).get_or_create_context(user)
        await db.commit()
    except LookupError:
        await _stale(
            callback,
            db,
            "Активное дело уже завершено. Перевод в консультацию не выполнялся.",
        )
        return
    except (M1RejectionDecisionError, ActiveCaseRouteConflict, ValueError) as error:
        await db.rollback()
        await _safe_edit(
            callback,
            f"Перевод в консультацию не выполнен: {error}\n\nДело осталось в согласованном состоянии.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Атомарный перевод отклонённого M1 в M2 не завершён")
        await _safe_edit(
            callback,
            "Перевод временно не завершён. Изменение маршрута отменено целиком; откройте актуальное дело перед повтором.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
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
        "Документы и история остались в том же деле. Отдельное дублирующее обращение не создано.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Продолжите консультацию с сохранёнными данными.",
        reply_markup=one(
            primary,
            ("📄 Документы", "documents_open"),
            ("✉️ Написать команде", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "m1_rejected_close_confirm")
async def guarded_rejected_m1_close(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        result = await M1RejectionDecisionService(db).apply(
            client_id=user.id,
            decision=DECISION_CLOSE,
        )
        if result.outcome != "closed":
            await _stale(
                callback,
                db,
                "Статус дела уже изменился. Старая кнопка закрытия ничего не изменила.",
            )
            return
        await db.commit()
    except LookupError:
        await _stale(
            callback,
            db,
            "Обращение уже не активно. Повторное закрытие не выполнялось.",
        )
        return
    except (M1RejectionDecisionError, ValueError) as error:
        await db.rollback()
        await _safe_edit(
            callback,
            f"Закрытие не выполнено: {error}",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Атомарное закрытие отклонённого M1 не завершено")
        await _safe_edit(
            callback,
            "Обращение временно не закрыто. Ничего не потеряно и маршрут не изменён; откройте актуальное дело.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _safe_edit(
        callback,
        "✅ Обращение завершено.\n\n"
        "Оно больше не считается активным. Документы, переписка и история сохранены для просмотра. Новое обращение можно начать отдельно.",
        reply_markup=one(
            ("📁 Открыть архив обращения", "my_case_open"),
            ("🧮 Новое обращение", "calc_start"),
            ("🏠 Главная", "nav_home"),
        ),
    )


__all__ = ["router"]
