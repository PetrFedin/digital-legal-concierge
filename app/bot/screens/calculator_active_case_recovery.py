from __future__ import annotations

from datetime import date

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.calculator_draft import (
    CALCULATOR_CASE_ID,
    DRAFT_MARKER,
    activate_calculator_case_draft,
    draft_step_label,
    has_saved_calculator_draft,
)
from app.bot.case_callback_scope import (
    bound_case_callback,
    callback_matches_action,
    resolve_case_callback_scope,
)
from app.bot.client_case_view import CLIENT_ACTIONS, ClientAction
from app.bot.keyboards import one
from app.bot.screens import calculator
from app.domain.calculator.intake_service import CalculationIntakeService
from app.domain.statuses.case_statuses import CaseStatus

router = Router()

_RECOVERABLE_CASE_STATUSES = {
    CaseStatus.NEW.value,
    CaseStatus.CALCULATOR_STARTED.value,
}
_CALCULATOR_FIELDS = (
    "contract_price",
    "planned_transfer_date",
    "object_transferred",
    "actual_transfer_date",
)


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


async def _activate_durable_case_draft(
    state: FSMContext,
    db,
    *,
    case_id: int,
) -> dict | None:
    """Restore one Case from PostgreSQL, using Redis only as cutover fallback.

    The durable intake row is authoritative. A pre-PM-017 Redis draft may be
    imported once for migration compatibility, but after that PostgreSQL owns the
    accepted facts and can reconstruct the FSM after Redis loss/restart.
    """

    data = await activate_calculator_case_draft(state, case_id=int(case_id))
    service = CalculationIntakeService(db)
    durable = await service.draft_data(case_id=int(case_id))

    if durable is None:
        draft_case_id = int(data.get(CALCULATOR_CASE_ID) or 0)
        if has_saved_calculator_draft(data) and draft_case_id == int(case_id):
            await service.sync_from_draft(
                case_id=int(case_id),
                data=data,
                today=date.today(),
            )
            await db.commit()
            durable = await service.draft_data(case_id=int(case_id))

    if durable is None:
        return None

    next_data = dict(data)
    for key in _CALCULATOR_FIELDS:
        next_data.pop(key, None)
    next_data.update(durable)
    next_data[CALCULATOR_CASE_ID] = int(case_id)
    next_data[DRAFT_MARKER] = True
    await state.set_state(None)
    await state.set_data(next_data)
    return next_data


async def _show_recoverable_calculation(
    callback: CallbackQuery,
    state: FSMContext,
    db,
    *,
    case_id: int,
) -> None:
    # Switching from another unfinished calculator Case first snapshots that
    # Case's flat working set in Redis, then authoritative accepted values for
    # this exact Case are restored from PostgreSQL.
    data = await _activate_durable_case_draft(
        state,
        db,
        case_id=int(case_id),
    )
    if data is not None:
        await callback.message.edit_text(
            "📝 Незавершённый расчёт восстановлен\n\n"
            f"{calculator._draft_summary(data)}\n\n"
            f"Следующий шаг: {draft_step_label(data)}.\n"
            "Продолжите с сохранённого места. Начать заново можно только после отдельного подтверждения.",
            reply_markup=one(
                (
                    "▶️ Продолжить расчёт",
                    bound_case_callback("calc_resume", int(case_id)),
                ),
                (
                    "Начать заново",
                    bound_case_callback("calc_restart_confirm", int(case_id)),
                ),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    # There are no accepted durable answers for this exact Case. Create/reset
    # its Case-card intake before rendering the first question; Redis is not
    # allowed to become the sole business copy again.
    await CalculationIntakeService(db).reset(case_id=int(case_id))
    await db.commit()
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
        await _show_recoverable_calculation(
            callback,
            state,
            db,
            case_id=selected_id,
        )
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


__all__ = ["install_active_case_recovery_actions", "router"]
