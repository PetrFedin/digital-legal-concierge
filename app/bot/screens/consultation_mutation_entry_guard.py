from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.case_callback_scope import (
    callback_matches_action,
    resolve_case_callback_scope,
)
from app.bot.keyboards import one
from app.bot.screens import consultations as legacy_consultations
from app.domain.statuses.case_statuses import RouteCode

router = Router()


async def _resolve_booked_m2_entry(callback: CallbackQuery, db, *, action: str):
    scope = await resolve_case_callback_scope(
        callback,
        db,
        action=action,
        # The current action-center already prints "Обращение № ...". This lets
        # those raw buttons survive the v2 rollout while a stale message from a
        # different selected Case is rejected.
        allow_legacy_message_case_context=True,
    )
    if scope is None:
        return None
    case = scope.case
    if case is not None and str(case.route or "").upper() != RouteCode.M2.value:
        await callback.message.edit_text(
            "Эта кнопка относится к консультации, но выбранное обращение сейчас относится к другому маршруту. "
            "Запись, слот и деньги не изменены.",
            reply_markup=one(
                ("📁 Выбрать обращение", "my_cases_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return None
    return scope


@router.callback_query(lambda c: callback_matches_action(c.data, "consult_reschedule"))
async def guarded_consult_reschedule(callback: CallbackQuery, db):
    scope = await _resolve_booked_m2_entry(
        callback,
        db,
        action="consult_reschedule",
    )
    if scope is None:
        return
    await legacy_consultations.consult_reschedule(callback, db)


@router.callback_query(lambda c: callback_matches_action(c.data, "consult_cancel"))
async def guarded_consult_cancel(callback: CallbackQuery, db):
    scope = await _resolve_booked_m2_entry(
        callback,
        db,
        action="consult_cancel",
    )
    if scope is None:
        return
    await legacy_consultations.consult_cancel(callback, db)


__all__ = ["router"]
