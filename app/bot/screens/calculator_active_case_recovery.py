from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.calculator_draft import (
    draft_step_label,
    has_saved_calculator_draft,
)
from app.bot.client_case_view import CLIENT_ACTIONS, ClientAction
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.screens import calculator
from app.domain.statuses.case_statuses import CaseStatus

router = Router()

_RECOVERABLE_CASE_STATUSES = {
    CaseStatus.NEW.value,
    CaseStatus.CALCULATOR_STARTED.value,
}


def install_active_case_recovery_actions() -> None:
    """Give historical pre-calculation cases a real client continuation CTA."""

    CLIENT_ACTIONS[CaseStatus.NEW.value] = ClientAction(
        "Начать предварительный расчёт",
        "calc_start",
        "Продолжите предварительный расчёт в этом обращении — новое дело создаваться не будет.",
    )
    CLIENT_ACTIONS[CaseStatus.CALCULATOR_STARTED.value] = ClientAction(
        "Продолжить расчёт",
        "calc_start",
        "Вернитесь к предварительному расчёту. Сохранённый черновик будет предложен автоматически, если он доступен.",
    )


async def _show_recoverable_calculation(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    if has_saved_calculator_draft(data):
        await state.set_state(None)
        await callback.message.edit_text(
            "📝 Незавершённый расчёт восстановлен\n\n"
            f"{calculator._draft_summary(data)}\n\n"
            f"Следующий шаг: {draft_step_label(data)}.\n"
            "Продолжите с сохранённого места. Начать заново можно только после отдельного подтверждения.",
            reply_markup=one(
                ("▶️ Продолжить расчёт", "calc_resume"),
                ("Начать заново", "calc_restart_confirm"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    # Redis/FSM data may have legitimately expired after a historical crash.
    # Keep the same Case row and restart only the questionnaire; the result will
    # be saved into this existing case by CalculatorService.
    await calculator._start_fresh(callback, state)


@router.callback_query(lambda c: c.data == "calc_start")
async def recover_or_delegate_calc_start(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)

    if (
        case is not None
        and str(case.status) in _RECOVERABLE_CASE_STATUSES
        and str(case.route or "") != "M2"
    ):
        await _show_recoverable_calculation(callback, state)
        return

    # Preserve the current normal-path behaviour for no case, saved drafts and
    # every later M1/M2 stage. This early router exists only to break the
    # historical NEW/CALCULATOR_STARTED loop.
    await calculator.calc_start(callback, state, db)


__all__ = ["install_active_case_recovery_actions", "router"]
