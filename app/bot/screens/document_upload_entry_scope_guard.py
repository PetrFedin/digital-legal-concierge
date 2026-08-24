from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.case_callback_scope import callback_matches_action, resolve_case_callback_scope
from app.bot.screens import document_upload_binding_guard

router = Router()


@router.callback_query(
    lambda c: callback_matches_action(c.data, "documents_upload_open")
)
async def scoped_document_upload_entry(
    callback: CallbackQuery,
    state: FSMContext,
    db,
) -> None:
    """Open the existing chooser only after the current Case is proven.

    Fresh document action-center screens visibly carry the canonical Case number.
    Historical raw buttons are therefore still usable while that visible context
    matches the selected active Case, but an old Case A button pressed after the
    client switches to Case B fails closed. The existing FSM draft is not cleared
    when provenance fails.

    A future/fresh ``documents_upload_open:v2:<case_id>`` is accepted by the same
    boundary without introducing a second upload implementation.
    """

    scope = await resolve_case_callback_scope(
        callback,
        db,
        action="documents_upload_open",
        allow_legacy_message_case_context=True,
    )
    if scope is None or scope.case is None:
        return

    await document_upload_binding_guard._render_bound_chooser(
        callback,
        state,
        db,
    )


__all__ = ["router"]
