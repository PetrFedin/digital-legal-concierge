from __future__ import annotations

from app.bot.case_callback_scope import bound_case_callback
from app.bot.screens import my_case

_INSTALLED = False


def _bound_secondary_callback(callback: str, *, case_id: int) -> str:
    value = str(callback or "")
    if value == "documents_open":
        return bound_case_callback("documents_open", case_id)
    if value == "payments_open":
        return bound_case_callback("payments_open", case_id)
    if value == "consultation_result_open":
        return bound_case_callback("consultation_result_open", case_id)
    if value == "message_history":
        return f"message_history:v2:{case_id}:0"
    if value == "case_history_open":
        return f"case_history_open:v2:{case_id}"
    return value


def install_case_bound_navigation() -> None:
    """Make fresh My Case secondary navigation carry the exact Case id.

    Historical raw Telegram buttons remain supported by their provenance guards,
    but newly rendered screens no longer need to infer context from message text.
    The wrapper is installed after other My Case presentation patches so it
    preserves their labels/order and changes only callback provenance.
    """

    global _INSTALLED
    if _INSTALLED:
        return
    original = my_case._case_buttons

    def case_bound_buttons(view, **kwargs):
        case_id = int(view.case_id)
        return [
            (label, _bound_secondary_callback(callback, case_id=case_id))
            for label, callback in original(view, **kwargs)
        ]

    my_case._case_buttons = case_bound_buttons
    _INSTALLED = True


__all__ = ["install_case_bound_navigation"]
