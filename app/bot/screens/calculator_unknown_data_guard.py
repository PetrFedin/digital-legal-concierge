from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import CalculatorStates
from app.domain.consultations.consultation_intake import (
    ActiveCaseRouteConflict,
    ConsultationIntakeService,
)
from app.domain.statuses.case_statuses import CaseStatus
from app.models.user import User

router = Router()
logger = logging.getLogger(__name__)

_ALLOWED_SOURCE_STATUSES = {
    CaseStatus.NEW,
    CaseStatus.CALCULATOR_STARTED,
}
_EXPECTED_FSM = {
    "calc_unknown_price": CalculatorStates.waiting_contract_price.state,
    "calc_unknown_date": CalculatorStates.waiting_planned_transfer_date.state,
}


async def _safe_edit(callback: CallbackQuery, text: str, *, reply_markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
        await callback.answer("Этот экран уже актуален.")


def _status(case) -> CaseStatus | None:
    try:
        return case.status if isinstance(case.status, CaseStatus) else CaseStatus(str(case.status))
    except (TypeError, ValueError):
        return None


@router.callback_query(lambda c: c.data in _EXPECTED_FSM)
async def atomic_unknown_data_to_consultation(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    action = str(callback.data or "")
    expected_state = _EXPECTED_FSM[action]
    current_state = await state.get_state()
    if current_state != expected_state:
        # Inline keyboards remain clickable forever. Without the exact calculator
        # FSM step, this historical raw callback has no provenance and therefore
        # cannot authorize a route mutation.
        await db.rollback()
        await _safe_edit(
            callback,
            "ℹ️ Эта кнопка относится к другому шагу расчёта. Дело и введённые данные не изменены.\n\n"
            "Продолжите текущий шаг расчёта либо откройте актуальное дело.",
            reply_markup=one(
                ("▶️ Продолжить расчёт", "calc_start"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        # Serialize all no-data fallbacks per client. This covers the initial
        # state where the calculator legitimately has no Case row yet and
        # prevents two rapid callbacks from manufacturing duplicate cases.
        locked_user = (
            await db.execute(
                select(User).where(User.id == int(user.id)).with_for_update()
            )
        ).scalar_one_or_none()
        if locked_user is None:
            raise LookupError("Пользователь не найден")

        case = await ctx.case_service.get_active_case_for_user(user.id)
        if case is None:
            case = await ctx.case_service.create_case(
                client=locked_user,
                status=CaseStatus.NEW,
            )
        else:
            current_status = _status(case)
            if current_status not in _ALLOWED_SOURCE_STATUSES:
                raise ActiveCaseRouteConflict(
                    "За время расчёта активное дело перешло на другой этап"
                )

        reason = (
            "Клиент не знает стоимость объекта и выбрал консультацию"
            if action == "calc_unknown_price"
            else "Клиент не знает/не может подтвердить дату передачи и выбрал консультацию"
        )
        await ctx.case_service.transfer_to_m2(
            case=case,
            actor_type="client",
            actor_id=user.id,
            reason=reason,
        )
        context_case, consultation = await ConsultationIntakeService(db).get_or_create_context(user)
        if int(context_case.id) != int(case.id):
            raise ActiveCaseRouteConflict(
                "Активное обращение изменилось во время перехода к консультации"
            )
        await db.commit()
    except (LookupError, ActiveCaseRouteConflict, ValueError) as error:
        await db.rollback()
        await _safe_edit(
            callback,
            f"Переход к консультации не выполнен: {error}.\n\n"
            "Расчёт и текущее дело не переводились вручную. Откройте актуальное состояние перед повтором.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Atomic calculator no-data fallback to M2 failed")
        await _safe_edit(
            callback,
            "Консультация временно не открыта. Изменение маршрута отменено целиком; введённые данные расчёта сохранены в текущем шаге. Повторите действие позже.",
            reply_markup=one(
                ("🔄 Повторить переход", action),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await state.clear()
    description_ready = bool(str(consultation.client_description or "").strip())
    primary = (
        ("📅 Продолжить: выбрать время", "consult_booking_start")
        if description_ready
        else ("📝 Описать вопрос", "consult_subject_start")
    )
    await _safe_edit(
        callback,
        "💬 Открыта консультация\n\n"
        "Автоматический расчёт без этих данных был бы ненадёжным, поэтому мы не подставляли значения и не создавали фиктивный результат. "
        "Консультационное обращение создано один раз; оплата и время ещё не выбирались.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Опишите вопрос для юриста. После этого можно добавить документы и выбрать время.",
        reply_markup=one(
            primary,
            ("📄 Документы", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


__all__ = ["router"]
