from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.case_callback_scope import callback_matches_action, resolve_case_callback_scope
from app.bot.consultation_result import is_terminal_consultation, latest_case_consultation
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.screens import consultation_intake, consultation_results, m1_rejection_recovery
from app.domain.statuses.case_statuses import CaseStatus

router = Router()


def _status(case) -> CaseStatus | None:
    if case is None:
        return None
    try:
        return case.status if isinstance(case.status, CaseStatus) else CaseStatus(str(case.status))
    except (TypeError, ValueError):
        return None


async def _no_active_context(callback: CallbackQuery, db, state: FSMContext) -> None:
    """Allow a genuinely global contact entry only when no old Case is named."""

    # resolve_case_callback_scope intentionally rejects a raw legacy screen that
    # visibly names a Case when there is no selected active matter. If we arrive
    # here, the callback is a genuinely global current entry such as the contact
    # menu, so the existing M2 intake owns creation/continuation semantics.
    await consultation_intake.contact_lawyer(callback, db, state)


@router.callback_query(lambda c: callback_matches_action(c.data, "contact_lawyer"))
async def scoped_contact_lawyer(
    callback: CallbackQuery,
    db,
    state: FSMContext,
) -> None:
    """Route contact intent only after exact/visible Case provenance is checked.

    The historical callback is overloaded by three product flows. This boundary
    keeps those meanings but prevents an old Case A screen from becoming a Case B
    rejection decision or consultation continuation after cabinet selection
    changes.
    """

    value = str(callback.data or "")
    explicitly_bound = value.startswith("contact_lawyer:v2:")

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))

    if not active_cases and not explicitly_bound:
        # A global raw menu button has no Case context. A raw old case screen is
        # distinguished by resolve_case_callback_scope's visible Case conflict.
        scope = await resolve_case_callback_scope(
            callback,
            db,
            action="contact_lawyer",
            allow_legacy_message_case_context=True,
        )
        if scope is None:
            return
        await _no_active_context(callback, db, state)
        return

    scope = await resolve_case_callback_scope(
        callback,
        db,
        action="contact_lawyer",
        allow_legacy_message_case_context=True,
    )
    if scope is None or scope.case is None:
        return

    case = scope.case
    if _status(case) == CaseStatus.M1_REJECTED:
        await m1_rejection_recovery._show_options(callback, case)
        return

    consultation = await latest_case_consultation(db, case_id=int(case.id))
    if is_terminal_consultation(consultation):
        await consultation_results.terminal_contact_lawyer(
            callback,
            result_case=case,
            result_consultation=consultation,
        )
        return

    # The generic handler is still the single owner of normal M1/M2 contact and
    # new-consultation presentation. Scope validation above guarantees that its
    # active-context lookup resolves the same Case.
    await consultation_intake.contact_lawyer(callback, db, state)


__all__ = ["router"]
