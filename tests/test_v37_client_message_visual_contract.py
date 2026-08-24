from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_message_flow_repeats_context_now_and_one_main_step():
    source = read("app/bot/client_wording_patch.py")

    patch = source.split("# The question flow", 1)[1].split(
        "# Fresh history screens", 1
    )[0]
    assert "Обращение № {case_number}" in patch
    assert patch.count('"СЕЙЧАС\\n"') >= 4
    assert patch.count('"ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\\n"') >= 4
    assert "ещё не отправлен" in patch or "ещё не отправлено" in patch
    assert "ГЛАВНЫЫЙ" not in patch


def test_fresh_message_history_uses_exact_case_entry_for_new_message():
    source = read("app/bot/client_wording_patch.py")

    history = source.split("def case_bound_history_keyboard", 1)[1].split(
        "# Historical payments.py", 1
    )[0]
    assert 'bound_case_callback("message_create", int(case_id))' in history
    assert 'f"message_history:v2:{case_id}:{page}"' in history
    assert "message_history_guard._history_keyboard = case_bound_history_keyboard" in source
