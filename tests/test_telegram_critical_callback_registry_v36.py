import ast
import re
from pathlib import Path


SCREENS_DIR = Path("app/bot/screens")
BOT_ENTRY = Path("app/bot/bot.py")

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

CRITICAL_ROUTER_REGISTRATIONS = {
    "common.router",
    "calculator.router",
    "my_case.router",
    "document_action_center.router",
    "documents.router",
    "consultation_results.router",
    "payments.router",
    "consultations.router",
    "m1_stages.router",
    "messages.router",
    "history.router",
}


def _callback_decorators() -> list[tuple[Path, str]]:
    decorators: list[tuple[Path, str]] = []
    for path in sorted(SCREENS_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                if getattr(decorator.func, "attr", None) != "callback_query":
                    continue
                segment = ast.get_source_segment(source, decorator)
                if segment:
                    decorators.append((path, segment))
    return decorators


def _has_exact_callback_handler(decorators: list[tuple[Path, str]], callback: str) -> bool:
    quoted = re.escape(callback)
    patterns = (
        re.compile(rf"\b[A-Za-z_]\w*\.data\s*==\s*['\"]{quoted}['\"]"),
        re.compile(
            rf"\b[A-Za-z_]\w*\.data\s+in\s+[\[\(][^\]\)]*['\"]{quoted}['\"]"
        ),
        re.compile(rf"\bF\.data\.in_\([^)]*['\"]{quoted}['\"]"),
    )
    return any(pattern.search(segment) for _, segment in decorators for pattern in patterns)


def _has_prefix_callback_handler(decorators: list[tuple[Path, str]], prefix: str) -> bool:
    quoted = re.escape(prefix)
    pattern = re.compile(
        rf"\b[A-Za-z_]\w*\.data\.startswith\(\s*['\"]{quoted}['\"]\s*\)"
    )
    return any(pattern.search(segment) for _, segment in decorators)


def test_all_critical_exact_callbacks_have_router_handlers():
    decorators = _callback_decorators()
    missing = [
        callback
        for callback in sorted(CRITICAL_EXACT_CALLBACKS)
        if not _has_exact_callback_handler(decorators, callback)
    ]
    assert not missing, f"Critical Telegram callbacks without handlers: {missing}"


def test_all_critical_prefix_callbacks_have_router_handlers():
    decorators = _callback_decorators()
    missing = [
        prefix
        for prefix in sorted(CRITICAL_PREFIX_CALLBACKS)
        if not _has_prefix_callback_handler(decorators, prefix)
    ]
    assert not missing, f"Critical Telegram callback prefixes without handlers: {missing}"


def test_critical_screen_routers_are_registered_in_dispatcher():
    source = BOT_ENTRY.read_text(encoding="utf-8")
    start = source.index("for router in [")
    end = source.index("dispatcher.include_router(router)", start)
    registration_block = source[start:end]
    missing = sorted(
        router for router in CRITICAL_ROUTER_REGISTRATIONS if router not in registration_block
    )
    assert not missing, f"Critical Telegram routers are not registered: {missing}"


def test_completed_archive_buttons_use_only_guarded_navigation_callbacks():
    source = Path("app/bot/screens/my_case.py").read_text(encoding="utf-8")
    required = {
        '"documents_open"',
        '"payments_open"',
        '"case_history_open"',
        '"preview_calc_start"',
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


def test_stale_m1_callbacks_use_strict_m1_archive_scope_only():
    stage_source = Path("app/bot/screens/m1_stages.py").read_text(encoding="utf-8")
    stage_start = stage_source.index("async def _show_missing_or_completed_stage")
    stage_end = stage_source.index("@router.callback_query", stage_start)
    stage_block = stage_source[stage_start:stage_end]
    assert "latest_completed_strict_m1_case_for_user" in stage_block
    assert "latest_completed_m1_case_for_user" not in stage_block
    assert '"message_create"' not in stage_block

    payments_source = Path("app/bot/screens/payments.py").read_text(encoding="utf-8")
    payment_start = payments_source.index("async def _show_missing_m1_payment_case")
    payment_end = payments_source.index("async def start_payment", payment_start)
    payment_block = payments_source[payment_start:payment_end]
    assert "latest_completed_strict_m1_case_for_user" in payment_block
    assert "active_or_latest_completed_m1_case_for_user" not in payment_block
    assert '"message_create"' not in payment_block
