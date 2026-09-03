from __future__ import annotations

from app.bot.screens import message_history_guard, telegram_safety_composite
from app.bot.screens.message_history_guard import (
    _history_keyboard,
    _parse_history_target,
)


def _callback_data(markup) -> set[str]:
    return {
        str(button.callback_data)
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    }


def test_message_history_v2_parser_keeps_exact_case_and_page() -> None:
    assert _parse_history_target("message_history:v2:42:3") == (42, 3, False)
    assert _parse_history_target("message_history") == (None, 0, True)
    assert _parse_history_target("message_history:2") == (None, 2, True)


def test_message_history_pagination_is_case_bound() -> None:
    markup = _history_keyboard(
        case_id=42,
        page=1,
        total_pages=3,
        read_only=False,
        selected_same_case=True,
    )
    callbacks = _callback_data(markup)

    assert "message_history:v2:42:2" in callbacks
    assert "message_history:v2:42:0" in callbacks
    assert "message_history:v2:42:1" in callbacks
    assert "message_create" in callbacks


def test_non_selected_active_case_has_switch_cta_not_message_mutation() -> None:
    markup = _history_keyboard(
        case_id=42,
        page=0,
        total_pages=1,
        read_only=False,
        selected_same_case=False,
    )
    callbacks = _callback_data(markup)

    assert "my_case_select:v2:42" in callbacks
    assert "message_create" not in callbacks


def test_case_bound_history_guard_is_part_of_runtime_safety_composite() -> None:
    assert message_history_guard.router in telegram_safety_composite.router.sub_routers
