from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_persistent_my_case_opens_shared_live_view_without_callback_trampoline():
    direct = read("app/bot/screens/reply_menu_direct.py")

    assert '@router.message(lambda m: m.text in {"📁 Мое дело", "📁 Моё дело"})' in direct
    assert "await common._home_text(" in direct
    assert "view = await load_client_case_view(db, case)" in direct
    assert "markup = one(*my_case._case_buttons(view))" in direct
    assert "await message.answer(text, reply_markup=markup)" in direct
    assert "fabricating a CallbackQuery" in direct


def test_direct_my_case_keeps_draft_guard_and_same_case_actions():
    direct = read("app/bot/screens/reply_menu_direct.py")

    assert "await common._guard_message_draft(message, state)" in direct
    assert "await state.clear()" in direct
    assert "ctx.case_service.get_active_case_for_user(user.id)" in direct
    assert 'text.replace("🏠 Главная", "📁 МОЁ ДЕЛО", 1)' in direct


def test_direct_reply_router_precedes_old_common_trampoline():
    bot = read("app/bot/bot.py")
    common = read("app/bot/screens/common.py")

    assert "reply_menu_direct.router" in bot
    assert bot.index("reply_menu_direct.router") < bot.index("common.router")
    # Keep the old handler as compatibility/dead-code fallback until live
    # Telegram regression confirms the earlier exact handler is effective.
    assert 'reply_markup=one(\n            ("📁 Моё дело", "my_case_open")' in common
