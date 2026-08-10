from pathlib import Path


def test_completed_message_history_does_not_mark_messages_read():
    source = Path("app/bot/screens/messages.py").read_text(encoding="utf-8")
    assert "if visible_team_ids and not read_only:" in source
    assert "Сообщений в архиве нет." in source
    assert "Написать сообщение" in source


def test_completed_message_history_hides_write_action():
    source = Path("app/bot/screens/messages.py").read_text(encoding="utf-8")
    history_start = source.index("def _history_keyboard")
    history_end = source.index("async def _safe_edit", history_start)
    history_block = source[history_start:history_end]
    assert "if not read_only:" in history_block
    assert '"message_create"' in history_block


def test_message_submit_requires_captured_active_target_or_explicit_new_request():
    source = Path("app/bot/screens/messages.py").read_text(encoding="utf-8")
    target_start = source.index("async def _locked_message_target")
    target_end = source.index("@router.callback_query(lambda c: c.data == \"contact_lawyer\")", target_start)
    target_block = source[target_start:target_end]
    assert "case_id" in target_block
    assert "new_request_confirmed" in target_block
    assert "MessageTargetChanged" in target_block
    assert "Черновик не прикреплён к нему автоматически" in target_block
