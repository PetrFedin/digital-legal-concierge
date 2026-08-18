from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_unresolved_m2_received_money_states_are_intercepted_before_new_charge_flow():
    guard = read("app/bot/screens/payment_received_money_guard.py")

    assert "PaymentStatus.PAID_REVIEW.value" in guard
    assert "PaymentStatus.REFUND_PENDING.value" in guard
    assert "PaymentStatus.REFUND_DECLINED.value" in guard
    assert "PaymentCode.M2_CONSULTATION_PAYMENT.value" in guard
    assert 'str(case.route or "").upper() != RouteCode.M2.value' in guard


def test_payments_open_stays_read_only_while_received_money_is_unresolved():
    guard = read("app/bot/screens/payment_received_money_guard.py")
    block = guard.split('@router.callback_query(lambda c: c.data == "payments_open")', 1)[1].split(
        '@router.callback_query(lambda c: c.data == "consult_pay")', 1
    )[0]

    assert "await payments.payments(callback, db)" in block
    assert "guard_active_m2_payment_list" in block
    # Conflict path deliberately bypasses materialization in the archive guard.
    assert block.index("if conflict is None:") < block.index("await payments.payments(callback, db)")


def test_consult_pay_tells_client_not_to_pay_or_change_slot_until_money_is_resolved():
    guard = read("app/bot/screens/payment_received_money_guard.py")
    block = guard.split('@router.callback_query(lambda c: c.data == "consult_pay")', 1)[1]

    assert "Повторная оплата сейчас не нужна" in block
    assert "Бот не создаст вторую ссылку" in block
    assert "не попросит выбрать другой слот" in block
    assert "Сначала команда должна завершить сверку предыдущих денег" in block
    assert '("💳 Посмотреть оплаты", "payments_open")' in block
    assert '("✉️ Написать команде", "message_create")' in block
    assert '("📁 Моё дело", "my_case_open")' in block


def test_received_money_guard_uses_scalars_before_rollback_and_precedes_archive_router():
    guard = read("app/bot/screens/payment_received_money_guard.py")
    bot = read("app/bot/bot.py")

    assert '"case_id": case_id' in guard
    assert '"payment_id": int(payment.id)' in guard
    assert '"status": str(payment.status)' in guard
    assert "payment_received_money_guard.router" in bot
    assert bot.index("payment_received_money_guard.router") < bot.index("payment_archive_guard.router")
    assert bot.index("payment_received_money_guard.router") < bot.index("payments.router")
