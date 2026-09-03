from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_selected_case_without_terminal_result_does_not_fall_back_to_another_case():
    source = read("app/bot/screens/consultation_results.py")
    handler = source.split(
        "async def consultation_result_open(callback: CallbackQuery, db):", 1
    )[1].split(
        '@router.callback_query(lambda c: callback_matches_action(c.data, "consult_follow_up_start"))',
        1,
    )[0]

    assert "if active_case is not None:" in handler
    assert "case_id=case_id" in handler
    assert "if latest is None:" in handler
    assert "Результат другого дела здесь не показывается" in handler
    assert '("📁 Выбрать обращение", "my_cases_open")' in handler

    active_branch, no_active_branch = handler.split("    else:\n        latest =", 1)
    assert "latest_terminal_client_consultation(db, client_id=user.id)" not in active_branch
    assert "latest_terminal_client_consultation(db, client_id=user.id)" in no_active_branch


def test_no_selected_active_case_can_still_open_latest_terminal_archive_result():
    source = read("app/bot/screens/consultation_results.py")
    handler = source.split(
        "async def consultation_result_open(callback: CallbackQuery, db):", 1
    )[1].split(
        '@router.callback_query(lambda c: callback_matches_action(c.data, "consult_follow_up_start"))',
        1,
    )[0]

    assert "else:\n        latest = await latest_terminal_client_consultation(db, client_id=user.id)" in handler
    assert "case, consultation = latest" in handler
    assert "await _render_result(callback, case=case, consultation=consultation)" in handler
