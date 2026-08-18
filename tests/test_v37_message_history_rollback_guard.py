from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_callback_history_captures_case_id_before_rollback():
    guard = read("app/bot/screens/message_history_guard.py")

    assert "case_id = int(case.id)" in guard
    assert "service.list_case_messages(case_id, limit=100)" in guard
    rollback_at = guard.index("await db.rollback()")
    mark_block = guard.index("service.mark_lawyer_messages_read(", rollback_at)
    assert "case_id," in guard[mark_block : mark_block + 180]
    assert "Do not access `case` after this point" in guard


def test_navigation_guard_owns_first_page_and_pagination_before_legacy_handler():
    nav = read("app/bot/screens/navigation_history_guard.py")
    bot = read("app/bot/bot.py")

    assert "message_history_guard.present_message_history" in nav
    assert '@router.callback_query(lambda c: c.data == "message_history")' in nav
    assert 'c.data.startswith("message_history:")' in nav
    assert "async def logical_message_history_page" in nav
    assert bot.index("navigation_history_guard.router") < bot.index("messages.router")


def test_direct_reply_history_also_uses_scalar_after_rollback():
    direct = read("app/bot/screens/reply_menu_direct.py")
    block = direct.split('@router.message(lambda m: m.text == "💬 Переписка")', 1)[1].split(
        '@router.message(lambda m: m.text == "✉️ Новый вопрос")', 1
    )[0]

    assert "case_id = int(case.id)" in block
    assert "service.list_case_messages(case_id, limit=100)" in block
    rollback_at = block.index("await db.rollback()")
    mark_at = block.index("service.mark_lawyer_messages_read(", rollback_at)
    assert "case_id," in block[mark_at : mark_at + 180]
    assert "case.id" not in block[rollback_at:]
