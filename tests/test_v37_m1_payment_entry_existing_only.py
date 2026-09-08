from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_payment_stage_guard_owns_raw_and_case_bound_m1_payment_entries():
    guard = read("app/bot/screens/payment_stage_binding_guard.py")
    bot = read("app/bot/bot.py")

    assert "def _stage_action" in guard
    assert "callback_matches_action(value, action)" in guard
    assert '@router.callback_query(lambda c: _stage_action(c.data) is not None)' in guard
    assert "resolve_case_callback_scope(" in guard
    assert "allow_legacy_message_case_context=True" in guard
    assert bot.index("telegram_safety_composite.router") < bot.index("payments.router")
    assert bot.index("telegram_safety_composite.router") < bot.index("m1_stages.router")


def test_client_m1_payment_entry_never_synthesizes_missing_obligation():
    guard = read("app/bot/screens/payment_stage_binding_guard.py")

    exact = guard.split("async def open_exact_stage_payment", 1)[1]
    assert "service.list_case_payments(case.id)" in exact
    assert "if payment is None:" in exact
    assert "Новый платёж из Telegram автоматически не создавался" in exact
    assert "get_or_create_payment(" not in exact


def test_success_fee_cannot_be_opened_by_client_from_money_received_intermediate_state():
    guard = read("app/bot/screens/payment_stage_binding_guard.py")

    entry = guard.split("async def stage_payment_is_exact_confirmation_only", 1)[1].split(
        "async def open_exact_stage_payment", 1
    )[0]
    assert 'action == "pay_success_fee" and current_status == CaseStatus.M1_MONEY_RECEIVED' in entry
    assert "Клиентская кнопка не может создать его или перевести дело дальше" in entry


def test_payment_stage_recovery_keeps_exact_case_message_action():
    guard = read("app/bot/screens/payment_stage_binding_guard.py")

    assert 'bound_case_callback("message_create", case_id)' in guard
    assert '"СЕЙЧАС"' in guard
    assert '"ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ"' in guard
