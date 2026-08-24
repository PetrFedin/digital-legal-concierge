from pathlib import Path

from app.bot.case_callback_scope import bind_payment_case_action

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_my_case_binds_all_case_sensitive_actions():
    for action in (
        "consult_subject_start",
        "consult_description_start",
        "consult_booking_start",
        "consult_slot_open",
        "consult_pay",
        "consult_reschedule",
        "consult_cancel",
        "consult_follow_up_start",
        "consultation_booked_open",
        "contact_lawyer",
        "message_create",
        "documents_open",
        "payments_open",
        "consultation_result_open",
    ):
        assert bind_payment_case_action(action, 73) == f"{action}:v2:73"


def test_truly_global_navigation_remains_unbound():
    for action in ("nav_home", "my_case_open", "my_cases_open"):
        assert bind_payment_case_action(action, 73) == action


def test_my_case_renderer_applies_case_binding_to_primary_action():
    source = read("app/bot/screens/my_case.py")

    assert "bind_payment_case_action(action.callback, case_id)" in source
    assert "next_action:v2:{case_id}:{action_key}" in source


def test_final_my_case_secondary_keyboard_is_exact_case_bound():
    source = read("app/bot/client_case_navigation.py")
    bot = read("app/bot/bot.py")

    assert 'bound_case_callback("documents_open", case_id)' in source
    assert 'bound_case_callback("payments_open", case_id)' in source
    assert 'bound_case_callback("consultation_result_open", case_id)' in source
    assert 'f"message_history:v2:{case_id}:0"' in source
    assert 'f"case_history_open:v2:{case_id}"' in source
    assert "install_case_bound_navigation()" in bot
    assert bot.index("client_archive.install_archive_button()") < bot.index(
        "install_case_bound_navigation()"
    )


def test_navigation_owner_accepts_bound_contextual_entries_but_back_stays_read_only():
    source = read("app/bot/screens/navigation_history_guard.py")

    assert 'callback_matches_action(c.data, "documents_open")' in source
    assert 'callback_matches_action(c.data, "payments_open")' in source
    assert 'callback_matches_action(c.data, "consultation_result_open")' in source
    assert "allow_legacy_message_case_context=True" in source
    assert "Back must never create/reconcile a payment" in source
    assert "payment_archive_guard" not in source


def test_contact_and_booked_entry_have_one_early_case_scope_owner():
    source = read("app/bot/screens/contact_lawyer_scope_guard.py")
    bot = read("app/bot/bot.py")

    assert 'callback_matches_action(c.data, "contact_lawyer")' in source
    assert 'callback_matches_action(c.data, "consultation_booked_open")' in source
    assert 'action="contact_lawyer"' in source
    assert 'action="consultation_booked_open"' in source
    assert "allow_legacy_message_case_context=True" in source
    assert 'bound_case_callback("message_create", case_id)' in source
    assert 'f"message_history:v2:{case_id}:0"' in source
    assert 'bound_case_callback("consultation_booked_open", case_id)' in source
    assert bot.index("contact_lawyer_scope_guard.router,") < bot.index(
        "consultation_results.router,"
    )
    assert bot.index("contact_lawyer_scope_guard.router,") < bot.index(
        "m1_rejection_recovery.router,"
    )
    assert bot.index("contact_lawyer_scope_guard.router,") < bot.index(
        "consultation_intake.router,"
    )
