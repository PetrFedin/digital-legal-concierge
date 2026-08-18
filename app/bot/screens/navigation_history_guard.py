from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.screens import (
    common,
    consultation_results,
    documents,
    history,
    message_history_guard,
    my_case,
    payments,
    service_contract,
)

router = Router()

_HISTORY_KEY = "_client_nav_history_v1"
_CURRENT_KEY = "_client_nav_current_v1"
_MAX_HISTORY = 12

_REPLAY_SAFE = frozenset(
    {
        "nav_home",
        "my_case_open",
        "documents_open",
        "documents_list_open",
        "documents_history_open",
        "payments_open",
        "case_history_open",
        "message_history",
        "consultation_result_open",
        "contract_open",
    }
)


def _clean_history(value) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[-_MAX_HISTORY:]:
        target = str(item or "")
        if target in _REPLAY_SAFE:
            result.append(target)
    return result


async def _snapshot(state: FSMContext) -> tuple[str | None, list[str]]:
    data = await state.get_data()
    current_raw = str(data.get(_CURRENT_KEY) or "")
    current = current_raw if current_raw in _REPLAY_SAFE else None
    return current, _clean_history(data.get(_HISTORY_KEY))


async def _record(state: FSMContext, target: str) -> None:
    if target not in _REPLAY_SAFE:
        return
    current, history_stack = await _snapshot(state)
    if current == target:
        return
    if current:
        history_stack.append(current)
        history_stack = history_stack[-_MAX_HISTORY:]
    await state.update_data(
        **{
            _CURRENT_KEY: target,
            _HISTORY_KEY: history_stack,
        }
    )


async def _reset(state: FSMContext, *, current: str = "nav_home") -> None:
    await state.update_data(
        **{
            _CURRENT_KEY: current,
            _HISTORY_KEY: [],
        }
    )


async def _pop(state: FSMContext) -> tuple[str | None, str | None]:
    current, history_stack = await _snapshot(state)
    target: str | None = None
    while history_stack:
        candidate = history_stack.pop()
        if candidate in _REPLAY_SAFE and candidate != current:
            target = candidate
            break
    if target:
        await state.update_data(
            **{
                _CURRENT_KEY: target,
                _HISTORY_KEY: history_stack,
            }
        )
    return current, target


async def _render_target(
    target: str,
    *,
    callback: CallbackQuery,
    state: FSMContext,
    db,
) -> bool:
    if target == "nav_home":
        await common.home(callback, db, state)
        await _reset(state)
        return True
    if target == "my_case_open":
        await my_case.my_case(callback, db)
        return True
    if target == "documents_open":
        await documents.docs(callback, db)
        return True
    if target == "documents_list_open":
        await documents.list_docs(callback, db)
        return True
    if target == "documents_history_open":
        await documents.documents_history(callback, db)
        return True
    if target == "payments_open":
        await payments.payments(callback, db)
        return True
    if target == "case_history_open":
        await history.case_history(callback, db)
        return True
    if target == "message_history":
        await message_history_guard.present_message_history(callback, db, state)
        return True
    if target == "consultation_result_open":
        await consultation_results.consultation_result_open(callback, db)
        return True
    if target == "contract_open":
        await service_contract.open_service_contract(callback, db)
        return True
    return False


async def _record_after(callback_handler, target: str, *, state: FSMContext):
    result = await callback_handler()
    await _record(state, target)
    return result


@router.callback_query(lambda c: c.data == "nav_home")
async def logical_home(callback: CallbackQuery, db, state: FSMContext):
    if await common._has_unsent_message_draft(state):
        return await common.home(callback, db, state)
    result = await common.home(callback, db, state)
    await _reset(state)
    return result


@router.callback_query(lambda c: c.data == "nav_cancel")
async def logical_cancel(callback: CallbackQuery, db, state: FSMContext):
    if await common._has_unsent_message_draft(state):
        return await common.cancel(callback, state, db)
    result = await common.cancel(callback, state, db)
    await _reset(state)
    return result


@router.callback_query(lambda c: c.data == "nav_back")
async def logical_back(callback: CallbackQuery, db, state: FSMContext):
    """Return to the previous replay-safe logical screen without mutating case state.

    Only idempotent/read-only screen entry callbacks are replayed. Mutating
    callbacks, payment creation, slot reservation and legal-stage actions are
    deliberately excluded, so Back can never repeat a business operation.
    """

    if await common._guard_callback_draft(callback, state):
        return

    current, target = await _pop(state)
    if target and await _render_target(
        target,
        callback=callback,
        state=state,
        db=db,
    ):
        return

    # UX fallback from the specification: if previous logical screen is not
    # available, prefer the case cabinet; from the case cabinet fall back Home.
    if current == "my_case_open":
        await common.home(callback, db, state)
        await _reset(state)
        return

    await my_case.my_case(callback, db)
    await _record(state, "my_case_open")


@router.callback_query(lambda c: c.data == "my_case_open")
async def logical_my_case(callback: CallbackQuery, db, state: FSMContext):
    return await _record_after(
        lambda: my_case.my_case(callback, db),
        "my_case_open",
        state=state,
    )


@router.callback_query(lambda c: c.data == "documents_open")
async def logical_documents(callback: CallbackQuery, db, state: FSMContext):
    return await _record_after(
        lambda: documents.docs(callback, db),
        "documents_open",
        state=state,
    )


@router.callback_query(lambda c: c.data == "documents_list_open")
async def logical_documents_list(callback: CallbackQuery, db, state: FSMContext):
    return await _record_after(
        lambda: documents.list_docs(callback, db),
        "documents_list_open",
        state=state,
    )


@router.callback_query(lambda c: c.data == "documents_history_open")
async def logical_documents_history(callback: CallbackQuery, db, state: FSMContext):
    return await _record_after(
        lambda: documents.documents_history(callback, db),
        "documents_history_open",
        state=state,
    )


@router.callback_query(lambda c: c.data == "payments_open")
async def logical_payments(callback: CallbackQuery, db, state: FSMContext):
    return await _record_after(
        lambda: payments.payments(callback, db),
        "payments_open",
        state=state,
    )


@router.callback_query(lambda c: c.data == "case_history_open")
async def logical_case_history(callback: CallbackQuery, db, state: FSMContext):
    return await _record_after(
        lambda: history.case_history(callback, db),
        "case_history_open",
        state=state,
    )


@router.callback_query(lambda c: c.data == "message_history")
async def logical_message_history(callback: CallbackQuery, db, state: FSMContext):
    if await common._has_unsent_message_draft(state):
        return await message_history_guard.present_message_history(callback, db, state)
    return await _record_after(
        lambda: message_history_guard.present_message_history(callback, db, state),
        "message_history",
        state=state,
    )


@router.callback_query(
    lambda c: bool(c.data) and c.data.startswith("message_history:")
)
async def logical_message_history_page(
    callback: CallbackQuery,
    db,
    state: FSMContext,
):
    """Keep pagination on the same logical screen and the same stable DB path."""

    return await message_history_guard.present_message_history(callback, db, state)


@router.callback_query(lambda c: c.data == "consultation_result_open")
async def logical_consultation_result(callback: CallbackQuery, db, state: FSMContext):
    return await _record_after(
        lambda: consultation_results.consultation_result_open(callback, db),
        "consultation_result_open",
        state=state,
    )


@router.callback_query(lambda c: c.data == "contract_open")
async def logical_contract(callback: CallbackQuery, db, state: FSMContext):
    return await _record_after(
        lambda: service_contract.open_service_contract(callback, db),
        "contract_open",
        state=state,
    )


__all__ = ["router"]
