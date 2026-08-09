from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_my_case_has_explicit_terminal_card_after_active_scope_disappears():
    source = read("app/bot/screens/my_case.py")

    assert "latest_completed_m1_case_for_user" in source
    assert "📁 ИТОГ ДЕЛА" in source
    assert "✅ Дело завершено" in source
    assert "progress_bar(100)" in source
    assert "Действий по этому делу больше не требуется" in source
    assert "только для просмотра" in source


def test_completed_home_remains_discoverable_without_exposing_active_case_menu():
    source = read("app/bot/screens/common.py")

    assert "completed_m1 = await latest_completed_m1_case_for_user" in source
    assert "✅ Последнее дело завершено" in source
    assert 'return "\\n".join(lines), False, COMPLETED_M1_ACTION' in source
    assert "Итог, история и платежи сохранены в режиме просмотра" in source


def test_completed_history_and_payment_ledger_use_read_only_scope():
    history = read("app/bot/screens/history.py")
    payments = read("app/bot/screens/payments.py")

    assert "active_or_latest_completed_m1_case_for_user" in history
    assert "История завершённого дела" in history
    assert 'if completed:' in history
    assert "active_or_latest_completed_m1_case_for_user" in payments
    assert "Оплаты завершённого дела" in payments
    assert "новые платежи из этого архива не создаются" in payments


def test_payment_creation_still_requires_active_case_after_archive_read_support():
    payments = read("app/bot/screens/payments.py")
    start = payments.index("async def start_payment")
    section = payments[start:]

    assert "get_active_case_for_user(user.id)" in section
    assert "active_or_latest_completed_m1_case_for_user" not in section.split(
        "@router.callback_query(lambda c: c.data.startswith(\"pay_open:\"))",
        1,
    )[0]
