from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.screens import document_action_center, documents

router = Router()


async def _has_active_document_context(callback: CallbackQuery, db) -> bool:
    """Return whether the document request belongs to an active-case cabinet.

    Selected/sole active Case stays on the modern action center. Several active
    Cases with no selected context also stay there so its existing fail-closed
    selector/recovery is preserved. Only a client with *no active matters* may
    fall through to the completed read-only archive renderer.
    """

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    selected = await ctx.case_service.get_selected_case_for_user(
        int(user.id),
        include_terminal=False,
    )
    if selected is not None:
        return True
    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
    return bool(active_cases)


@router.callback_query(lambda c: c.data == "documents_open")
async def route_documents_home(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    if await _has_active_document_context(callback, db):
        await document_action_center._render_home(callback, state, db)
        return

    # No active Case: use the existing route-complete archive projection. It
    # resolves the latest completed M1/M2 Case and never exposes upload/replace
    # mutations for a terminal matter.
    await documents._render_documents_home(callback, db)


@router.callback_query(lambda c: c.data == "documents_list_open")
async def route_documents_list(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    if await _has_active_document_context(callback, db):
        await document_action_center.exact_replacement_document_list(
            callback,
            state,
            db,
        )
        return

    await documents._render_current_documents(callback, db, 0)


__all__ = ["router"]
