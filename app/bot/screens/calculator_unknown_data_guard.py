from __future__ import annotations

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.calculator_draft import CALCULATOR_CASE_ID
from app.bot.case_callback_scope import bound_case_callback
from app.bot.keyboards import one
from app.bot.states import CalculatorStates

router = Router()

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


def _stale_step_buttons(case_id: int) -> list[tuple[str, str]]:
    buttons: list[tuple[str, str]] = []
    if case_id > 0:
        buttons.append(
            (
                "▶️ Продолжить расчёт этого обращения",
                bound_case_callback("calc_recover", case_id),
            )
        )
    buttons.extend(
        [
            ("📁 Моё дело", "my_case_open"),
            ("🧮 Новый расчёт", "preview_calc_start"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    return buttons


@router.callback_query(lambda c: c.data in _EXPECTED_FSM)
async def legacy_unbound_unknown_data(callback: CallbackQuery, state: FSMContext, db):
    """Fail closed for historical unknown-price/date buttons without Case id.

    Current calculator screens emit exact ``:v2:<case_id>`` callbacks and the
    canonical calculator handler owns the M2 transition. A raw historical token
    carries no Case provenance; even when FSM currently happens to contain a
    Case id and matching step, using that ambient context to mutate the route
    would let an old Telegram message act on a newer selected matter.

    The raw callback therefore performs no Case/consultation mutation. When an
    exact calculator Case is still present in FSM we offer an explicit bound
    recovery action; otherwise the user can choose the current Case or start a
    genuinely new calculation.
    """

    data = await state.get_data()
    try:
        case_id = int(data.get(CALCULATOR_CASE_ID) or 0)
    except (TypeError, ValueError):
        case_id = 0

    await db.rollback()
    await _safe_edit(
        callback,
        "ℹ️ Эта кнопка относится к старой версии расчёта и не содержит номер обращения. "
        "Переход к консультации не выполнен, дело и введённые данные не изменены.\n\n"
        "Откройте актуальное обращение или продолжите его расчёт через кнопку с точной привязкой к делу.",
        reply_markup=one(*_stale_step_buttons(case_id)),
    )


__all__ = ["router"]