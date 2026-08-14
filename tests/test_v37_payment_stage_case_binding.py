from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_raw_stage_payment_buttons_are_confirmation_only():
    source = read("app/bot/screens/payment_stage_binding_guard.py")

    assert '"pay_start_30000"' in source
    assert '"pay_court_70000"' in source
    assert '"pay_success_fee"' in source
    raw = source.split("async def legacy_stage_payment_is_confirmation_only", 1)[1].split(
        "async def open_exact_stage_payment", 1
    )[0]
    assert "PaymentService" not in raw
    assert "create_payment_link" not in raw
    assert "change_status(" not in raw
    assert "db.commit" not in raw
    assert "pay_stage:v2:" in source


def test_bound_payment_locks_exact_owned_active_case_and_exact_code():
    source = read("app/bot/screens/payment_stage_binding_guard.py")

    handler = source.split("async def open_exact_stage_payment", 1)[1]
    assert "Case.id == int(case_id)" in handler
    assert "Case.client_id == int(user.id)" in handler
    assert ".with_for_update()" in handler
    assert "int(active.id) != int(case.id)" in handler
    assert "_status(case) != expected_status" in handler
    assert "service.list_case_payments(case.id)" in handler
    assert "str(item.payment_code) == str(payment_code)" in handler


def test_client_cannot_create_missing_business_payment_from_telegram():
    source = read("app/bot/screens/payment_stage_binding_guard.py")
    handler = source.split("async def open_exact_stage_payment", 1)[1]

    assert "if payment is None:" in handler
    assert "Новый платёж из Telegram автоматически не создавался" in handler
    assert "create_payment(" not in handler
    assert "ensure_payment(" not in handler
    assert "change_status(" not in handler


def test_opening_link_does_not_mark_payment_paid_or_advance_legal_case():
    source = read("app/bot/screens/payment_stage_binding_guard.py")
    handler = source.split("async def open_exact_stage_payment", 1)[1]

    assert "service.create_payment_link(payment)" in handler
    assert "PaymentStatus.PAID" not in handler
    assert "change_status(" not in handler
    assert "transition" not in handler
    assert "Следующий этап откроется только после серверного подтверждения" in handler


def test_payment_guard_is_mounted_before_archive_and_legacy_payment_handlers():
    bot = read("app/bot/bot.py")
    composite = read("app/bot/screens/telegram_safety_composite.py")

    assert "payment_stage_binding_guard" in composite
    assert "router.include_router(payment_stage_binding_router)" in composite
    assert bot.index("telegram_safety_composite.router,") < bot.index(
        "payment_archive_guard.router,"
    )
    assert bot.index("telegram_safety_composite.router,") < bot.index("payments.router,")
