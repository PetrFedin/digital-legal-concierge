from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.states import CalculatorStates

DRAFT_MARKER = "_calculator_draft_saved"
DRAFT_LAST_STATE = "_calculator_draft_last_state"
CALCULATOR_CASE_ID = "calculator_case_id"
CALCULATOR_DRAFTS_BY_CASE = "_calculator_drafts_by_case"
_CALCULATOR_STATE_PREFIX = f"{CalculatorStates.__name__}:"
_NAV_CALLBACKS = frozenset({"nav_home", "nav_cancel", "nav_back"})
_NAV_MESSAGES = frozenset(
    {
        "/start",
        "/menu",
        "/cancel",
        "🏠 Главная",
        "Отмена",
        # Persistent Telegram cabinet navigation must pause, not erase, an
        # unfinished calculation. This is critical once one client may have
        # several active Cases/calculator drafts at the same time.
        "🧮 Рассчитать неустойку",
        "📁 Мое дело",
        "📁 Моё дело",
        "📄 Документы",
        "💬 Связаться с юристом",
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
    # The case binding itself is meaningful even before the first answer. If a
    # client opens the calculator and immediately goes Home, losing case_id
    # would orphan CALCULATOR_STARTED and the next click could create a second
    # matter for the same draft.
    return bool(data.get(CALCULATOR_CASE_ID)) or any(
        key in data and data.get(key) not in (None, "")
        for key in _CALCULATOR_FIELDS
    )


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


def _drafts_by_case(data: dict) -> dict[str, dict]:
    raw = data.get(CALCULATOR_DRAFTS_BY_CASE)
    if not isinstance(raw, dict):
        return {}

    result: dict[str, dict] = {}
    for raw_case_id, raw_snapshot in raw.items():
        try:
            case_id = int(raw_case_id)
        except (TypeError, ValueError):
            continue
        if case_id <= 0 or not isinstance(raw_snapshot, dict):
            continue
        snapshot = {CALCULATOR_CASE_ID: case_id}
        for key in _CALCULATOR_FIELDS:
            if key in raw_snapshot:
                snapshot[key] = raw_snapshot[key]
        if raw_snapshot.get(DRAFT_MARKER):
            snapshot[DRAFT_MARKER] = True
        if raw_snapshot.get(DRAFT_LAST_STATE):
            snapshot[DRAFT_LAST_STATE] = str(raw_snapshot[DRAFT_LAST_STATE])
        result[str(case_id)] = snapshot
    return result


def _current_snapshot(
    data: dict,
    *,
    current_state: str | None,
) -> tuple[int, dict] | None:
    try:
        case_id = int(data.get(CALCULATOR_CASE_ID) or 0)
    except (TypeError, ValueError):
        return None
    if case_id <= 0:
        return None

    snapshot: dict = {
        CALCULATOR_CASE_ID: case_id,
        DRAFT_MARKER: True,
    }
    for key in _CALCULATOR_FIELDS:
        if key in data:
            snapshot[key] = data[key]
    last_state = current_state or data.get(DRAFT_LAST_STATE)
    if last_state:
        snapshot[DRAFT_LAST_STATE] = str(last_state)
    return case_id, snapshot


def _navigation_data(data: dict) -> dict:
    return {
        key: value
        for key, value in data.items()
        if str(key).startswith(_NAV_DATA_PREFIX)
    }


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

    current = _current_snapshot(snapshot, current_state=current_state)
    if current is not None:
        case_id, case_snapshot = current
        drafts = _drafts_by_case(snapshot)
        drafts[str(case_id)] = case_snapshot
        snapshot[CALCULATOR_DRAFTS_BY_CASE] = drafts

    await state.set_state(None)
    await state.set_data(snapshot)
    return True


async def activate_calculator_case_draft(
    state: FSMContext,
    *,
    case_id: int,
) -> dict:
    """Activate one Case's calculator draft without losing another Case's draft.

    The flat FSM fields remain the active calculator working set for backwards
    compatibility with the existing handlers. Paused drafts are namespaced in a
    Redis/Memory FSM map by Case id. Switching Case A → Case B snapshots A first,
    then restores only B. No legal/business state is created by this helper.
    """

    target_case_id = int(case_id)
    if target_case_id <= 0:
        raise ValueError("case_id должен быть положительным")

    current_state = await state.get_state()
    data = dict(await state.get_data())
    drafts = _drafts_by_case(data)

    current = _current_snapshot(data, current_state=current_state)
    current_case_id = current[0] if current is not None else 0
    if current is not None and current_case_id != target_case_id:
        drafts[str(current_case_id)] = current[1]

    if current is not None and current_case_id == target_case_id:
        active = dict(current[1])
    else:
        active = dict(drafts.get(str(target_case_id), {}))
        if active:
            active[CALCULATOR_CASE_ID] = target_case_id
        else:
            active = {CALCULATOR_CASE_ID: target_case_id}

    next_data = _navigation_data(data)
    if drafts:
        next_data[CALCULATOR_DRAFTS_BY_CASE] = drafts
    next_data.update(active)

    await state.set_state(None)
    await state.set_data(next_data)
    return next_data


async def start_fresh_calculator_case(
    state: FSMContext,
    *,
    case_id: int,
) -> dict:
    """Start/reset one Case calculator while retaining drafts of other Cases."""

    target_case_id = int(case_id)
    if target_case_id <= 0:
        raise ValueError("case_id должен быть положительным")

    current_state = await state.get_state()
    data = dict(await state.get_data())
    drafts = _drafts_by_case(data)
    current = _current_snapshot(data, current_state=current_state)
    if current is not None and current[0] != target_case_id:
        drafts[str(current[0])] = current[1]

    # An explicit restart of the target Case discards only that Case's paused
    # calculator answers. Other active matters remain recoverable.
    drafts.pop(str(target_case_id), None)
    next_data = _navigation_data(data)
    if drafts:
        next_data[CALCULATOR_DRAFTS_BY_CASE] = drafts
    next_data[CALCULATOR_CASE_ID] = target_case_id

    await state.set_state(None)
    await state.set_data(next_data)
    return next_data


async def finish_calculator_case(
    state: FSMContext,
    *,
    case_id: int,
) -> None:
    """Remove only one completed/rerouted Case draft and preserve the rest."""

    target_case_id = int(case_id)
    data = dict(await state.get_data())
    drafts = _drafts_by_case(data)
    drafts.pop(str(target_case_id), None)

    try:
        current_case_id = int(data.get(CALCULATOR_CASE_ID) or 0)
    except (TypeError, ValueError):
        current_case_id = 0

    if current_case_id != target_case_id:
        # Defensive path: another Case is already active in the flat working
        # set. Preserve it and only remove the completed target from the map.
        data[CALCULATOR_DRAFTS_BY_CASE] = drafts
        if not drafts:
            data.pop(CALCULATOR_DRAFTS_BY_CASE, None)
        await state.set_data(data)
        return

    next_data = _navigation_data(data)
    if drafts:
        next_data[CALCULATOR_DRAFTS_BY_CASE] = drafts
    await state.set_state(None)
    await state.set_data(next_data)


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
    """Restore calculator drafts after global/persistent cabinet navigation.

    Global Home/Cancel and persistent cabinet buttons may leave the calculator,
    but they must not erase entered values. The active flat calculator working
    set is snapshotted into a per-Case draft map, the canonical navigation
    handler is allowed to clear/change FSM state, then the paused calculator
    working set is restored. Starting or recovering another Case later performs
    an explicit per-Case switch.
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
        ) and (
            is_calculator_state(current_state)
            or has_meaningful_calculator_data(current_data)
        )
        if should_restore:
            current_data[DRAFT_MARKER] = True
            if current_state:
                current_data[DRAFT_LAST_STATE] = current_state
            current = _current_snapshot(
                current_data,
                current_state=current_state,
            )
            if current is not None:
                drafts = _drafts_by_case(current_data)
                drafts[str(current[0])] = current[1]
                current_data[CALCULATOR_DRAFTS_BY_CASE] = drafts

        result = await handler(event, data)

        if should_restore:
            # A navigation handler may clear the context. Restore calculator
            # answers and exact case_id, but keep post-navigation breadcrumb
            # metadata so Back cannot resurrect the same breadcrumb forever.
            post_navigation_data = dict(await state.get_data())
            for key, value in post_navigation_data.items():
                if str(key).startswith(_NAV_DATA_PREFIX):
                    current_data[key] = value
            await state.set_state(None)
            await state.set_data(current_data)
        return result


__all__ = [
    "CALCULATOR_CASE_ID",
    "CALCULATOR_DRAFTS_BY_CASE",
    "CalculatorDraftNavigationMiddleware",
    "DRAFT_LAST_STATE",
    "DRAFT_MARKER",
    "activate_calculator_case_draft",
    "clear_calculator_draft_metadata",
    "draft_step",
    "draft_step_label",
    "finish_calculator_case",
    "has_saved_calculator_draft",
    "mark_calculator_draft_paused",
    "start_fresh_calculator_case",
]
