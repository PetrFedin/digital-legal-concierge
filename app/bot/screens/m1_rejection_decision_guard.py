from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.types import CallbackQuery

from app.bot.case_callback_scope import bound_case_callback
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

_LEGACY_UNBOUND_MUTATIONS = {
    "m1_rejected_to_m2",
    "m1_rejected_close_confirm",
}


async def _safe_edit(callback: CallbackQuery, text: str, *, reply_markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
    except (TelegramNetworkError, TelegramServerError):
        logger.warning("Telegram не обновил экран решения после отказа M1")


def _case_id(callback: CallbackQuery, action: str) -> int | None:
    value = str(callback.data or "")
    prefix = f"{action}:v2:"
    if not value.startswith(prefix):
        return None
    try:
        case_id = int(value[len(prefix) :])
    except ValueError:
        return None
    return case_id if case_id > 0 else None


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


@router.callback_query(lambda c: c.data in _LEGACY_UNBOUND_MUTATIONS)
async def legacy_rejection_mutation_is_navigation_only(callback: CallbackQuery, db):
    # Historical keyboards do not contain a case identifier. They can remain in
    # Telegram after another case is created, therefore they never authorize a
    # route switch or closure anymore.
    await _stale(
        callback,
        db,
        "Эта кнопка относится к старому экрану отказа и не содержит номер дела. Ничего не изменено. Откройте актуальную карточку и выберите действие заново.",
    )


@router.callback_query(
    lambda c: bool(c.data) and c.data.startswith("m1_rejected_to_m2:v2:")
)
async def guarded_rejected_m1_to_m2(callback: CallbackQuery, db):
    case_id = _case_id(callback, "m1_rejected_to_m2")
    if case_id is None:
        await _stale(callback, db, "Некорректная кнопка. Дело не изменено.")
        return

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        result = await M1RejectionDecisionService(db).apply(
            client_id=user.id,
            case_id=case_id,
            decision=DECISION_TO_M2,
        )
        if result.outcome != "m2_intake":
            await _stale(
                callback,
                db,
                "Решение по этому делу уже изменилось. Старая кнопка ничего не изменила.",
            )
            return

        # Keep route switch and consultation record atomic. The intake service
        # must resolve the same exact case that the bound callback authorized.
        context_case, consultation = await ConsultationIntakeService(db).get_or_create_context(user)
        if int(context_case.id) != int(result.case.id):
            raise ActiveCaseRouteConflict(
                "Активное обращение изменилось во время перехода к консультации"
            )

        # Snapshot every presentation value before the transaction boundary.
        converted_case_id = int(result.case.id)
        converted_case_number = str(result.case.case_number)
        description_ready = bool(str(consultation.client_description or "").strip())
        await db.commit()
    except LookupError:
        await _stale(
            callback,
            db,
            "Дело из этого сообщения уже завершено, удалено или недоступно. Перевод не выполнялся.",
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

    primary = (
        (
            "📅 Продолжить: выбрать время",
            bound_case_callback("consult_booking_start", converted_case_id),
        )
        if description_ready
        else (
            "📝 Описать вопрос",
            bound_case_callback("consult_subject_start", converted_case_id),
        )
    )
    await _safe_edit(
        callback,
        "✅ МАРШРУТ ОБНОВЛЁН\n"
        f"Обращение № {converted_case_number}\n"
        "Услуга: личная консультация\n\n"
        "СЕЙЧАС\n"
        "Обращение переведено из M1 в консультационный маршрут. Документы и история остались в этом же обращении; дубликат не создан.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        + (
            "Выберите актуальные дату и время консультации."
            if description_ready
            else "Опишите вопрос для консультации."
        ),
        reply_markup=one(
            primary,
            ("📄 Документы", "documents_open"),
            (
                "✉️ Написать команде",
                bound_case_callback("message_create", converted_case_id),
            ),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(
    lambda c: bool(c.data) and c.data.startswith("m1_rejected_close_confirm:v2:")
)
async def guarded_rejected_m1_close(callback: CallbackQuery, db):
    case_id = _case_id(callback, "m1_rejected_close_confirm")
    if case_id is None:
        await _stale(callback, db, "Некорректная кнопка. Дело не изменено.")
        return

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        result = await M1RejectionDecisionService(db).apply(
            client_id=user.id,
            case_id=case_id,
            decision=DECISION_CLOSE,
        )
        if result.outcome != "closed":
            await _stale(
                callback,
                db,
                "Статус именно этого дела уже изменился. Повторное закрытие не выполнялось.",
            )
            return
        closed_case_number = str(result.case.case_number)
        await db.commit()
    except LookupError:
        await _stale(
            callback,
            db,
            "Дело из этого сообщения уже не активно или недоступно. Повторное закрытие не выполнялось.",
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
        "✅ ОБРАЩЕНИЕ ЗАВЕРШЕНО\n"
        f"Обращение № {closed_case_number}\n\n"
        "СЕЙЧАС\n"
        "Обращение больше не считается активным. Документы, переписка и история сохранены в режиме просмотра.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Действий по закрытому обращению не требуется. Новое обращение создаётся отдельно.",
        reply_markup=one(
            ("📁 Открыть архив обращения", "my_case_open"),
            ("🧮 Новое обращение", "preview_calc_start"),
            ("🏠 Главная", "nav_home"),
        ),
    )


__all__ = ["router"]
