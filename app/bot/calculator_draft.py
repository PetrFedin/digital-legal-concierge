from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.states import CalculatorStates

DRAFT_MARKER = "_calculator_draft_saved"
DRAFT_LAST_STATE = "_calculator_draft_last_state"
_CALCULATOR_STATE_PREFIX = f"{CalculatorStates.__name__}:"
_NAV_CALLBACKS = frozenset({"nav_home", "nav_cancel", "nav_back"})
_NAV_MESSAGES = frozenset(
    {
        "/start",
        "/menu",
        "/cancel",
        "🏠 Главная",
        "Отмена",
    }
)
_CALCULATOR_FIELDS = frozenset(
    {
        "contract_price",
        "planned_transfer_date",
        "object_transferred",
        "actual_transfer_date",
    }
)
_NAV_DATA_PREFIX = "_client_nav_"


def is_calculator_state(value: str | None) -> bool:
    return bool(value and str(value).startswith(_CALCULATOR_STATE_PREFIX))


def has_meaningful_calculator_data(data: dict) -> bool:
    return any(key in data and data.get(key) not in (None, "") for key in _CALCULATOR_FIELDS)


def has_saved_calculator_draft(data: dict) -> bool:
    return bool(data.get(DRAFT_MARKER) and has_meaningful_calculator_data(data))


def draft_step(data: dict) -> str:
    if not data.get("contract_price"):
        return "price"
    if not data.get("planned_transfer_date"):
        return "planned_date"
    if "object_transferred" not in data:
        return "transfer_status"
    if bool(data.get("object_transferred")) and not data.get("actual_transfer_date"):
        return "actual_date"
    # A complete value set normally gets committed immediately. If the UI was
    # interrupted between the last answer and calculation, return to the final
    # safe confirmation/input boundary rather than silently creating a case.
    return "actual_date" if bool(data.get("object_transferred")) else "transfer_status"


def draft_step_label(data: dict) -> str:
    return {
        "price": "стоимость объекта",
        "planned_date": "дата передачи по ДДУ",
        "transfer_status": "статус передачи объекта",
        "actual_date": "дата фактической передачи",
    }[draft_step(data)]


async def mark_calculator_draft_paused(state: FSMContext) -> bool:
    current_state = await state.get_state()
    data = await state.get_data()
    if not is_calculator_state(current_state) and not has_saved_calculator_draft(data):
        return False
    if not has_meaningful_calculator_data(data) and not is_calculator_state(current_state):
        return False
    snapshot = dict(data)
    snapshot[DRAFT_MARKER] = True
    if current_state:
        snapshot[DRAFT_LAST_STATE] = current_state
    await state.set_state(None)
    await state.set_data(snapshot)
    return True


async def clear_calculator_draft_metadata(state: FSMContext) -> dict:
    data = dict(await state.get_data())
    data.pop(DRAFT_MARKER, None)
    data.pop(DRAFT_LAST_STATE, None)
    await state.set_data(data)
    return data


def _is_navigation_event(event) -> bool:
    if isinstance(event, CallbackQuery):
        return str(event.data or "") in _NAV_CALLBACKS
    if isinstance(event, Message):
        return str(event.text or "") in _NAV_MESSAGES
    return False


class CalculatorDraftNavigationMiddleware:
    """Restore a calculator draft after global navigation clears FSM state.

    Global Home/Cancel handlers intentionally clear unrelated FSM flows. The UX
    contract for the calculator is different: leaving the form pauses it and
    must not delete already entered values. We snapshot only calculator data,
    let the normal navigation handler render the canonical home screen, then
    restore the draft with no active FSM state. The next ``calc_start`` resumes
    at the first incomplete step.
    """

    async def __call__(self, handler, event, data):
        state: FSMContext | None = data.get("state")
        if state is None or not _is_navigation_event(event):
            return await handler(event, data)

        current_state = await state.get_state()
        current_data = dict(await state.get_data())
        should_restore = (
            is_calculator_state(current_state)
            or has_saved_calculator_draft(current_data)
        ) and (is_calculator_state(current_state) or has_meaningful_calculator_data(current_data))
        if should_restore:
            current_data[DRAFT_MARKER] = True
            if current_state:
                current_data[DRAFT_LAST_STATE] = current_state

        result = await handler(event, data)

        if should_restore:
            # A navigation handler may clear the context. Restore calculator
            # answers, but keep the post-navigation breadcrumb metadata so a
            # Back click cannot resurrect the same breadcrumb forever.
            post_navigation_data = dict(await state.get_data())
            for key, value in post_navigation_data.items():
                if str(key).startswith(_NAV_DATA_PREFIX):
                    current_data[key] = value
            await state.set_state(None)
            await state.set_data(current_data)
        return result


__all__ = [
    "CalculatorDraftNavigationMiddleware",
    "DRAFT_LAST_STATE",
    "DRAFT_MARKER",
    "clear_calculator_draft_metadata",
    "draft_step",
    "draft_step_label",
    "has_saved_calculator_draft",
    "mark_calculator_draft_paused",
]
