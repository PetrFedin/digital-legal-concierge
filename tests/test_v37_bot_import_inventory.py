from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_every_screen_module_imported_by_bot_physically_exists():
    source = read("app/bot/bot.py")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != "app.bot.screens":
            continue
        imported.update(alias.name for alias in node.names)

    assert imported
    missing = [
        name
        for name in sorted(imported)
        if not (ROOT / "app" / "bot" / "screens" / f"{name}.py").exists()
        and not (ROOT / "app" / "bot" / "screens" / name / "__init__.py").exists()
    ]
    assert missing == []


def test_missing_legal_stage_compatibility_router_is_mutation_free():
    source = read("app/bot/screens/m1_legal_stages.py")
    assert "router = Router()" in source
    assert "change_status(" not in source
    assert "get_or_create_payment" not in source
    assert "process_successful_payment" not in source


def test_stale_m1_view_guard_precedes_legacy_m1_stage_handlers():
    guard = read("app/bot/screens/m1_stale_view_guard.py")
    archive = read("app/bot/screens/payment_archive_guard.py")
    bot = read("app/bot/bot.py")
    assert 'callback.data != "poa_instruction"' in guard
    assert 'callback.data != "court_status"' in guard
    assert "latest_completed_case_for_user" in guard
    assert "router.include_router(m1_stale_view_guard_router)" in archive
    assert bot.index("payment_archive_guard.router,") < bot.index("m1_stages.router,")


def test_client_dev_payment_fixture_is_runtime_disabled_outside_local_test():
    patch = read("app/bot/client_wording_patch.py")
    bot = read("app/bot/bot.py")
    block = patch.split("def local_test_fake_payments_only", 1)[1].split(
        "payments.fake_payments_enabled", 1
    )[0]
    assert 'settings.payment_provider' in block
    assert 'settings.app_env' in block
    assert '{"local", "test"}' in block
    assert "demo_mode" not in block
    assert "payments.fake_payments_enabled = local_test_fake_payments_only" in patch
    assert bot.index("install_client_wording()") < bot.index("for router in (")


def test_unknown_callback_fallback_is_last_and_preserves_active_fsm():
    fallback = read("app/bot/screens/fallback.py")
    bot = read("app/bot/bot.py")
    assert "FallbackUnknownCallbackFilter" in fallback
    assert "current_state = await state.get_state()" in fallback
    assert "Завершите текущий шаг или отмените его командой /cancel" in fallback
    assert bot.index("fallback.router,") > bot.index("m1_stages.router,")
    assert bot.index("fallback.router,") > bot.index("payments.router,")
