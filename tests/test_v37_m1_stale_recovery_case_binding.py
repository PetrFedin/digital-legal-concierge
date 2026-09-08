from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_stale_m1_recovery_binds_current_primary_and_message_actions():
    source = read("app/bot/screens/m1_stale_view_guard.py")

    buttons = source.split("def _active_buttons", 1)[1].split(
        "async def _render_stale", 1
    )[0]
    assert "bind_payment_case_action(action.callback, case_id)" in buttons
    assert 'bound_case_callback("message_create", case_id)' in buttons


def test_stale_m1_screen_repeats_exact_case_and_canonical_visual_sequence():
    source = read("app/bot/screens/m1_stale_view_guard.py")

    active = source.split("if case is not None:", 1)[1].split(
        "completed = await", 1
    )[0]
    assert 'f"Обращение № {case_number}' in active
    assert '"СЕЙЧАС\\n"' in active
    assert 'f"ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\\n{next_text}' in active
    assert "Ничего не изменено" in active
