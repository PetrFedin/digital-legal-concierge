from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_back_does_not_replay_callback_parsers_with_nav_back_token():
    source = read("app/bot/screens/navigation_history_guard.py")
    replay = source.split("async def _render_target", 1)[1].split(
        "async def _record_after", 1
    )[0]

    assert "history._render_history(" in replay
    assert "legacy_unbound=False" in replay
    assert "_render_current_message_history(callback, db, state)" in replay
    assert "history.case_history(callback, db)" not in replay
    assert "message_history_guard.present_message_history(callback, db, state)" not in replay
    assert "service_contract.open_service_contract" not in replay


def test_contract_delivery_is_not_part_of_replay_safe_stack():
    source = read("app/bot/screens/navigation_history_guard.py")
    safe_block = source.split("_REPLAY_SAFE = frozenset(", 1)[1].split(")\n\n\ndef _clean_history", 1)[0]
    contract_handler = source.split("async def logical_contract", 1)[1]

    assert '"contract_open"' not in safe_block
    assert "not recorded as replay-safe" in contract_handler
    assert "service_contract.open_service_contract(callback, db)" in contract_handler


def test_back_to_payments_remains_presentation_only():
    source = read("app/bot/screens/navigation_history_guard.py")
    replay = source.split("async def _render_target", 1)[1].split(
        "async def _record_after", 1
    )[0]

    assert "await payments.payments(callback, db)" in replay
    assert "payment_archive_guard" not in replay
    assert "create_payment_link" not in replay
    assert "reconcile(" not in replay


def test_explicit_payments_click_resumes_current_exact_m2_before_navigation_router():
    direct = read("app/bot/screens/payment_list_resume_guard.py")
    bot = read("app/bot/bot.py")

    assert 'callback_matches_action(c.data, "payments_open")' in direct
    assert "navigation_history_guard._direct_case_context_is_safe" in direct
    assert 'action="payments_open"' in direct
    assert "payment_archive_guard.guard_active_m2_payment_list" in direct
    assert 'navigation_history_guard._record(state, "payments_open")' in direct
    assert bot.index("payment_list_resume_guard.router,") < bot.index(
        "navigation_history_guard.router,"
    )
    assert "nav_back" not in direct.split("@router.callback_query", 1)[1].split(
        "async def direct_payment_list_resume", 1
    )[0]


def test_back_to_messages_preserves_multi_case_fail_closed_and_draft_guard():
    source = read("app/bot/screens/navigation_history_guard.py")
    helper = source.split("async def _render_current_message_history", 1)[1].split(
        "async def _render_target", 1
    )[0]

    assert "messages._guard_existing_draft" in helper
    assert "selected_case is None and len(active_cases) > 1" in helper
    assert "Back не выбирает дело автоматически" in helper
    assert "latest_completed_case_for_user" in helper
    assert "visible_team_ids and not read_only and selected_same_case" in helper
