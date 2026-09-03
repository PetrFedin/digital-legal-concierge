from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_offline_court_screen_points_to_real_payment_status_not_dead_payment_callback():
    source = read("app/bot/screens/no_payment_legal.py")

    branch = source.split("elif status in {", 1)[1].split(
        "elif status in {", 1
    )[0]
    assert 'CaseStatus.M1_WAITING_PAYMENT_70000' in branch
    assert '("💳 Проверить оплаты", "payments_open")' in branch
    assert '"pay_court_70000"' not in branch
    assert "Онлайн-оплата отключена" in branch
    assert "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ" in branch


def test_offline_court_screen_keeps_question_entry_case_bound():
    source = read("app/bot/screens/no_payment_legal.py")

    assert 'bound_case_callback("message_create", case_id)' in source
    assert 'f"Обращение № {case_number}' in source
