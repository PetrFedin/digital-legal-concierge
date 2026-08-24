from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.case_callback_scope import callback_matches_action, resolve_case_callback_scope

# Early, mutation-free boundary for M1 client informational screens. Proof-bearing
# legal facts (claim sent, court stage/evidence, enforcement/money received) remain
# owned by authenticated lawyer/admin services. The handlers below only verify
# exact Case provenance before delegating to the existing presentation functions.
router = Router()


async def _delegate_exact_context(
    callback: CallbackQuery,
    db,
    *,
    action: str,
    renderer_name: str,
) -> None:
    scope = await resolve_case_callback_scope(
        callback,
        db,
        action=action,
        allow_legacy_message_case_context=True,
    )
    if scope is None:
        return

    # The resolver also guarantees that a bound action matches the currently
    # selected active Case. Reuse the established M1 renderer rather than
    # creating a second business/state implementation.
    from app.bot.screens import m1_stages

    renderer = getattr(m1_stages, renderer_name)
    await renderer(callback, db)


@router.callback_query(lambda c: callback_matches_action(c.data, "poa_instruction"))
async def guarded_poa_instruction(callback: CallbackQuery, db):
    await _delegate_exact_context(
        callback,
        db,
        action="poa_instruction",
        renderer_name="poa_instruction",
    )


@router.callback_query(lambda c: callback_matches_action(c.data, "court_status"))
async def guarded_court_status(callback: CallbackQuery, db):
    await _delegate_exact_context(
        callback,
        db,
        action="court_status",
        renderer_name="court_status",
    )


__all__ = ["router"]
