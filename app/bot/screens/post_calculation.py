from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.cases.post_calculation_decision_service import (
    CHOICE_M1,
    CHOICE_M2,
    CHOICE_POSTPONE,
    PostCalculationDecisionError,
    PostCalculationDecisionService,
)
from app.domain.consultations.consultation_intake import (
    ActiveCaseRouteConflict,
    ConsultationIntakeService,
)
from app.domain.statuses.case_statuses import CaseStatus

router = Router()
logger = logging.getLogger(__name__)


async def _safe_edit(callback: CallbackQuery, text: str, *, reply_markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
        await callback.answer("Этот экран уже актуален.")


def _status(case) -> CaseStatus:
    return case.status if isinstance(case.status, CaseStatus) else CaseStatus(str(case.status))


async def _current_case_recovery(callback: CallbackQuery, case) -> None:
    try:
        status = _status(case)
    except (TypeError, ValueError):
        status = None

    if status is not None and status.value.startswith("M1_"):
        text = (
            "ℹ️ Эта кнопка относится к старому экрану расчёта. Ведение дела уже начато. "
            "Ничего не изменено — откройте текущую карточку."
        )
    elif status is not None and status.value.startswith("M2_"):
        text = (
            "ℹ️ Эта кнопка относится к старому экрану расчёта. Консультационный маршрут уже начат. "
            "Ничего не изменено — откройте текущую карточку."
        )
    else:
        text = (
            "ℹ️ Состояние дела уже изменилось. Старая кнопка ничего не изменила. "
            "Откройте актуальную карточку — там показан следующий доступный шаг."
        )

    await _safe_edit(
        callback,
        text,
        reply_markup=one(
            ("📁 Открыть текущее дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


async def _apply_choice(callback: CallbackQuery, db, choice: str):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        result = await PostCalculationDecisionService(db).apply(
            client_id=user.id,
            choice=choice,
        )
    except LookupError:
        await db.rollback()
        await _safe_edit(
            callback,
            "Активное дело не найдено. Расчёт не изменён. Можно начать новый расчёт или вернуться на главную.",
            reply_markup=one(
                ("🧮 Новый расчёт", "calc_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return user, None
    except PostCalculationDecisionError as error:
        await db.rollback()
        logger.warning("Некорректный выбор после расчёта: %s", error)
        await _safe_edit(
            callback,
            "Не удалось применить этот выбор. Дело не изменено — откройте актуальную карточку.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return user, None
    return user, result


@router.callback_query(lambda c: c.data == "calc_decision_open")
async def decision_open(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await _safe_edit(
            callback,
            "Сохранённое активное дело больше не найдено. Начните новый расчёт или вернитесь на главную.",
            reply_markup=one(
                ("🧮 Новый расчёт", "calc_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    status = _status(case)
    if status == CaseStatus.CALCULATED:
        await _safe_edit(
            callback,
            "🧭 Что делать после расчёта\n\n"
            "Расчёт сохранён. Выберите дальнейший путь — решение можно не принимать прямо сейчас.\n\n"
            "⚖️ Ведение дела — передача документов юристу и дальнейшее сопровождение.\n"
            "💬 Консультация — описать вопрос, при желании добавить документы и выбрать время.",
            reply_markup=one(
                ("⚖️ Продолжить ведение дела", "calc_continue_m1"),
                ("💬 Перейти к консультации", "calc_to_m2"),
                ("Пока ничего не менять", "calc_postpone"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if status == CaseStatus.CLIENT_DECISION:
        await _safe_edit(
            callback,
            "🧭 Вы выбрали ведение дела\n\n"
            "Следующий обязательный шаг — подтвердить согласие на обработку персональных данных. "
            "До подтверждения документы юристу не передаются.\n\n"
            "Решение ещё можно изменить и перейти к консультации.",
            reply_markup=one(
                ("📄 Перейти к согласию", "consent_open"),
                ("💬 Вместо этого — консультация", "calc_to_m2"),
                ("Пока ничего не менять", "calc_postpone"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if str(status.value).startswith("M1_"):
        await _safe_edit(
            callback,
            "Эта кнопка относится к более раннему этапу. Ведение дела уже начато — показан безопасный переход к текущему состоянию.",
            reply_markup=one(
                ("📁 Открыть текущее дело", "my_case_open"),
                ("📄 Документы", "documents_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if str(status.value).startswith("M2_"):
        await _safe_edit(
            callback,
            "Эта кнопка относится к более раннему этапу. Сейчас активно консультационное обращение.",
            reply_markup=one(
                ("📁 Открыть текущее дело", "my_case_open"),
                ("💬 Продолжить консультацию", "contact_lawyer"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _safe_edit(
        callback,
        "Состояние дела уже изменилось. Откройте актуальную карточку — там показан текущий следующий шаг.",
        reply_markup=one(
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "calc_continue_m1")
async def continue_m1_after_calculation(callback: CallbackQuery, db):
    _user, result = await _apply_choice(callback, db, CHOICE_M1)
    if result is None:
        return
    if result.outcome != "m1_consent_required":
        await db.rollback()
        await _current_case_recovery(callback, result.case)
        return

    await db.commit()
    await _safe_edit(
        callback,
        "⚖️ Ведение дела выбрано\n\n"
        "Расчёт сохранён. Услуга ещё не начата и документы ещё не переданы юристу.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Откройте согласие на обработку персональных данных и подтвердите его отдельно. "
        "Только после этого откроется загрузка документов.",
        reply_markup=one(
            ("📄 Перейти к согласию", "consent_open"),
            ("💬 Вместо этого — консультация", "calc_to_m2"),
            ("Пока ничего не менять", "calc_postpone"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "calc_to_m2")
async def continue_m2_after_calculation(callback: CallbackQuery, db):
    user, result = await _apply_choice(callback, db, CHOICE_M2)
    if result is None:
        return
    if result.outcome != "m2_intake":
        await db.rollback()
        await _current_case_recovery(callback, result.case)
        return

    try:
        # The route transition and consultation context are committed together.
        # If context creation fails, the route choice is rolled back as well so
        # the client is never stranded in M2 without a consultation record.
        await ConsultationIntakeService(db).get_or_create_context(user)
        await db.commit()
    except ActiveCaseRouteConflict as error:
        await db.rollback()
        await _safe_edit(
            callback,
            f"Консультация не создана.\n\n{error}",
            reply_markup=one(
                ("📁 Открыть текущее дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось создать M2-контекст после выбора консультации")
        await _safe_edit(
            callback,
            "Не удалось безопасно открыть консультацию. Выбор маршрута не сохранён — расчёт остался на прежнем этапе. Повторите действие или откройте текущее дело.",
            reply_markup=one(
                ("🔄 Повторить консультацию", "calc_to_m2"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _safe_edit(
        callback,
        "💬 Консультация выбрана\n\n"
        "Расчёт сохранён и консультационное обращение создано. Оплата и время ещё не выбирались.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Опишите ситуацию и конкретный вопрос для юриста. После сохранения вопроса можно добавить документы и выбрать свободное время.",
        reply_markup=one(
            ("📝 Описать вопрос", "consult_subject_start"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "calc_postpone")
async def postpone_after_calculation(callback: CallbackQuery, db):
    _user, result = await _apply_choice(callback, db, CHOICE_POSTPONE)
    if result is None:
        return
    if result.outcome != "postponed":
        await db.rollback()
        await _current_case_recovery(callback, result.case)
        return

    await db.commit()
    await _safe_edit(
        callback,
        "✅ Расчёт сохранён\n\n"
        "Вы пока не выбрали услугу. Ведение дела и консультация не запущены, платёж не создавался. "
        "К выбору можно вернуться позже из карточки дела.",
        reply_markup=one(
            ("🧭 Вернуться к выбору", "calc_decision_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )
