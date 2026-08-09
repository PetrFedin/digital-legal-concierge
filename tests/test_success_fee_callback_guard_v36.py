from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def handler_source() -> str:
    source = (ROOT / "app/bot/screens/m1_stages.py").read_text(encoding="utf-8")
    start = source.index('@router.callback_query(lambda c: c.data == "pay_success_fee")')
    return source[start:]


def test_success_fee_callback_cannot_open_payment_before_actual_recovery():
    source = handler_source()

    assert "CaseStatus.M1_ENFORCEMENT" in source
    assert "сначала команда должна" in source
    assert "зафиксировать фактическое получение денег" in source


def test_success_fee_callback_recalculates_authoritative_fee_and_reuses_only_matching_active_payment():
    source = handler_source()

    assert "amount = await service.estimate_success_fee_for_case(case.id)" in source
    assert "payment_code=PaymentCode.M1_SUCCESS_FEE" in source
    assert "amount=amount" in source
    assert "select(Payment)" not in source


def test_success_fee_callback_rolls_back_on_stale_or_financial_mismatch():
    source = handler_source()

    assert "except (RuntimeError, ValueError) as error:" in source
    assert "await db.rollback()" in source
    assert "Данные дела не изменены" in source
    assert "await db.commit()" in source
