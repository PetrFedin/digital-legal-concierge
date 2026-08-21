from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.calculator_draft import (
    CALCULATOR_CASE_ID,
    draft_step_label,
    has_saved_calculator_draft,
)
from app.bot.case_callback_scope import (
    callback_matches_action,
    resolve_case_callback_scope,
)
from app.bot.client_case_view import CLIENT_ACTIONS, ClientAction
from app.bot.keyboards import one
from app.bot.screens import calculator
from app.domain.statuses.case_statuses import CaseStatus

router = Router()

_RECOVERABLE_CASE_STATUSES = {
    CaseStatus.NEW.value,
    CaseStatus.CALCULATOR_STARTED.value,
}


def install_active_case_recovery_actions() -> None:
    """Give historical pre-calculation Cases an explicit same-Case resume CTA.

    ``calc_start`` is the global product action for a new calculation/new Case.
    Recovery therefore has its own callback token and is Case-bound when emitted
    from My Case. This prevents router precedence from turning an explicit new
    calculation into a silent resume of another object's unfinished draft.
    """

    CLIENT_ACTIONS[CaseStatus.NEW.value] = ClientAction(
        "Продолжить предварительный расчёт",
        "calc_recover",
        "Продолжите предварительный расчёт именно в этом обращении. Новый расчёт по другому объекту запускается отдельной кнопкой «Рассчитать неустойку».",
    )
    CLIENT_ACTIONS[CaseStatus.CALCULATOR_STARTED.value] = ClientAction(
        "Продолжить расчёт",
        "calc_recover",
        "Вернитесь к расчёту этого обращения. Сохранённый черновик будет предложен автоматически, если он доступен.",
    )


async def _show_recoverable_calculation(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    case_id: int,
) -> None:
    data = await state.get_data()
    draft_case_id = int(data.get(CALCULATOR_CASE_ID) or 0)
    if has_saved_calculator_draft(data) and draft_case_id == int(case_id):
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
    # Bind the questionnaire to this exact Case before accepting any new input.
    await calculator._start_fresh(
        callback,
        state,
        case_id=int(case_id),
    )


@router.callback_query(lambda c: callback_matches_action(c.data, "calc_recover"))
async def recover_selected_calculation(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    """Resume one explicit unfinished Case; never create a replacement Case."""

    scope = await resolve_case_callback_scope(
        callback,
        db,
        action="calc_recover",
    )
    if scope is None:
        return

    case = scope.case
    if (
        case is None
        or str(case.status) not in _RECOVERABLE_CASE_STATUSES
        or str(case.route or "") == "M2"
    ):
        await callback.message.edit_text(
            "Эта кнопка восстановления больше не соответствует текущему этапу обращения. "
            "Данные не изменены и новое дело не создано.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🧮 Новый расчёт", "calc_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    try:
        selected = await scope.ctx.case_service.select_case_for_user(
            user_id=int(scope.user.id),
            case_id=int(case.id),
        )
        selected_id = int(selected.id)
        await db.commit()
    except Exception:
        await db.rollback()
        await callback.message.edit_text(
            "⚠️ Не удалось открыть сохранённый расчёт. Данные обращения не изменены. "
            "Выберите дело заново и повторите действие.",
            reply_markup=one(
                ("📁 Выбрать обращение", "my_cases_open"),
                ("🧮 Новый расчёт", "calc_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _show_recoverable_calculation(
        callback,
        state,
        case_id=selected_id,
    )


__all__ = ["install_active_case_recovery_actions", "router"]
