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

_LEGACY_UNBOUND_CHOICES = {
    "calc_continue_m1",
    "calc_to_m2",
    "calc_postpone",
}


async def _safe_edit(callback: CallbackQuery, text: str, *, reply_markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
        await callback.answer("Этот экран уже актуален.")


def _status(case) -> CaseStatus:
    return case.status if isinstance(case.status, CaseStatus) else CaseStatus(str(case.status))


def _bound(action: str, case_id: int) -> str:
    return f"{action}:v2:{int(case_id)}"


def _case_id_from_bound(callback: CallbackQuery, action: str) -> int | None:
    value = str(callback.data or "")
    prefix = f"{action}:v2:"
    if not value.startswith(prefix):
        return None
    try:
        case_id = int(value[len(prefix) :])
    except ValueError:
        return None
    return case_id if case_id > 0 else None


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
            "ℹ️ Состояние дела из этого сообщения уже изменилось. Кнопка ничего не изменила. "
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


async def _apply_choice(
    callback: CallbackQuery,
    db,
    *,
    choice: str,
    expected_case_id: int,
):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        result = await PostCalculationDecisionService(db).apply(
            client_id=user.id,
            case_id=expected_case_id,
            choice=choice,
        )
    except LookupError:
        await db.rollback()
        await _safe_edit(
            callback,
            "Дело из этого сообщения больше не найдено или вам недоступно. Ничего не изменено.",
            reply_markup=one(
                ("📁 Открыть актуальное дело", "my_case_open"),
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
    case_id = int(case.id)
    if status == CaseStatus.CALCULATED:
        await _safe_edit(
            callback,
            "🧭 Что делать после расчёта\n\n"
            "Расчёт сохранён. Выберите дальнейший путь — решение можно не принимать прямо сейчас.\n\n"
            "⚖️ Ведение дела — передача документов юристу и дальнейшее сопровождение.\n"
            "💬 Консультация — описать вопрос, при желании добавить документы и выбрать время.",
            reply_markup=one(
                ("⚖️ Продолжить ведение дела", _bound("calc_continue_m1", case_id)),
                ("💬 Перейти к консультации", _bound("calc_to_m2", case_id)),
                ("Пока ничего не менять", _bound("calc_postpone", case_id)),
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
                ("📄 Перейти к согласию", _bound("consent_open", case_id)),
                ("💬 Вместо этого — консультация", _bound("calc_to_m2", case_id)),
                ("Пока ничего не менять", _bound("calc_postpone", case_id)),
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


@router.callback_query(lambda c: c.data in _LEGACY_UNBOUND_CHOICES)
async def legacy_unbound_choice_refresh(callback: CallbackQuery, db):
    # Historical calculator messages do not carry a case id. They are useful as
    # navigation only, but can no longer authorize a business mutation because
    # a newer case may now be active for the same Telegram user.
    await callback.answer("Обновляем безопасный экран выбора.")
    await decision_open(callback, db)


@router.callback_query(
    lambda c: bool(c.data) and c.data.startswith("calc_continue_m1:v2:")
)
async def continue_m1_after_calculation(callback: CallbackQuery, db):
    case_id = _case_id_from_bound(callback, "calc_continue_m1")
    if case_id is None:
        await decision_open(callback, db)
        return
    _user, result = await _apply_choice(
        callback,
        db,
        choice=CHOICE_M1,
        expected_case_id=case_id,
    )
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
            ("📄 Перейти к согласию", _bound("consent_open", int(result.case.id))),
            ("💬 Вместо этого — консультация", _bound("calc_to_m2", int(result.case.id))),
            ("Пока ничего не менять", _bound("calc_postpone", int(result.case.id))),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: bool(c.data) and c.data.startswith("calc_to_m2:v2:"))
async def continue_m2_after_calculation(callback: CallbackQuery, db):
    case_id = _case_id_from_bound(callback, "calc_to_m2")
    if case_id is None:
        await decision_open(callback, db)
        return
    user, result = await _apply_choice(
        callback,
        db,
        choice=CHOICE_M2,
        expected_case_id=case_id,
    )
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
        context_case, _consultation = await ConsultationIntakeService(db).get_or_create_context(user)
        if int(context_case.id) != int(result.case.id):
            raise ActiveCaseRouteConflict(
                "Активное обращение изменилось во время выбора консультации"
            )
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
                ("🔄 Повторить консультацию", _bound("calc_to_m2", case_id)),
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


@router.callback_query(lambda c: bool(c.data) and c.data.startswith("calc_postpone:v2:"))
async def postpone_after_calculation(callback: CallbackQuery, db):
    case_id = _case_id_from_bound(callback, "calc_postpone")
    if case_id is None:
        await decision_open(callback, db)
        return
    _user, result = await _apply_choice(
        callback,
        db,
        choice=CHOICE_POSTPONE,
        expected_case_id=case_id,
    )
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
