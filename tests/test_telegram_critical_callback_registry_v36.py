from pathlib import Path


SCREENS_DIR = Path("app/bot/screens")

CRITICAL_EXACT_CALLBACKS = {
    "nav_home",
    "my_case_open",
    "documents_open",
    "payments_open",
    "case_history_open",
    "message_create",
    "message_history",
    "consultation_result_open",
    "consultation_booked_open",
    "calc_start",
}

CRITICAL_PREFIX_CALLBACKS = {
    "next_action:",
    "pay_open:",
}


def _screen_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(SCREENS_DIR.glob("*.py"))
    )


def test_all_critical_exact_callbacks_have_router_handlers():
    source = _screen_source()
    missing = []
    for callback in sorted(CRITICAL_EXACT_CALLBACKS):
        exact_forms = (
            f'c.data == "{callback}"',
            f"c.data == '{callback}'",
            f'c.data in ["{callback}"',
            f"c.data in ['{callback}'",
        )
        if not any(form in source for form in exact_forms):
            missing.append(callback)

    assert not missing, f"Critical Telegram callbacks without handlers: {missing}"


def test_all_critical_prefix_callbacks_have_router_handlers():
    source = _screen_source()
    missing = []
    for prefix in sorted(CRITICAL_PREFIX_CALLBACKS):
        prefix_forms = (
            f'c.data.startswith("{prefix}")',
            f"c.data.startswith('{prefix}')",
        )
        if not any(form in source for form in prefix_forms):
            missing.append(prefix)

    assert not missing, f"Critical Telegram callback prefixes without handlers: {missing}"


def test_completed_archive_buttons_use_only_guarded_navigation_callbacks():
    source = Path("app/bot/screens/my_case.py").read_text(encoding="utf-8")
    required = {
        '"documents_open"',
        '"payments_open"',
        '"case_history_open"',
        '"calc_start"',
        '"nav_home"',
    }
    for callback_literal in required:
        assert callback_literal in source

    # Closed archive is read-only: it must not expose active-case mutation entry
    # points such as creating a message or confirming a payment from the archive.
    completed_block_start = source.index("async def _render_completed_case")
    completed_block_end = source.index("async def _render_case", completed_block_start)
    completed_block = source[completed_block_start:completed_block_end]
    assert '"message_create"' not in completed_block
    assert '"pay_start_30000"' not in completed_block
    assert '"pay_court_70000"' not in completed_block
    assert '"pay_success_fee"' not in completed_block
