from pathlib import Path

from app.bot.case_callback_scope import bind_payment_case_action

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_my_case_binds_all_case_sensitive_m2_actions():
    for action in (
        "consult_subject_start",
        "consult_description_start",
        "consult_booking_start",
        "consult_slot_open",
        "consult_pay",
        "consult_reschedule",
        "consult_cancel",
        "consult_follow_up_start",
    ):
        assert bind_payment_case_action(action, 73) == f"{action}:v2:73"


def test_read_only_my_case_navigation_remains_unbound():
    for action in ("documents_open", "payments_open", "message_create", "case_history_open"):
        assert bind_payment_case_action(action, 73) == action


def test_my_case_renderer_applies_case_binding_to_primary_action():
    source = read("app/bot/screens/my_case.py")

    assert "bind_payment_case_action(action.callback, case_id)" in source
    assert "next_action:v2:{case_id}:{action_key}" in source
