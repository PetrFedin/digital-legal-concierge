from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.case_callback_scope import callback_matches_action
from app.bot.screens import navigation_history_guard, payment_archive_guard

router = Router()


@router.callback_query(lambda c: callback_matches_action(c.data, "payments_open"))
async def direct_payment_list_resume(
    callback: CallbackQuery,
    db,
    state: FSMContext,
) -> None:
    """Resume the exact live M2 payment only on an explicit Payments click.

    `nav_back` never matches this router. Therefore logical Back can continue to
    render payment history without creating/reconciling a Payment, while a fresh
    exact Case `payments_open:v2:<case_id>` keeps the established product promise
    that the client can leave the original reservation screen and resume later.
    """

    if not await navigation_history_guard._direct_case_context_is_safe(
        callback,
        db,
        action="payments_open",
    ):
        return

    await payment_archive_guard.guard_active_m2_payment_list(callback, db)
    await navigation_history_guard._record(state, "payments_open")


__all__ = ["router"]
