from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_fixed_waiting_stages_have_admin_integrity_check_for_exact_payment_codes():
    source = read("app/api/staff_ui_guards.py")

    assert "CaseStatus.M1_WAITING_PAYMENT_30000" in source
    assert "PaymentCode.M1_INITIAL_PAYMENT.value" in source
    assert "CaseStatus.M1_WAITING_PAYMENT_70000" in source
    assert "PaymentCode.M1_COURT_PAYMENT.value" in source
    assert "m1_fixed_payment_obligation_missing" in source
    assert "Клиентский Telegram намеренно не создаёт такое обязательство сам" in source


def test_payment_code_normalization_handles_string_or_enum_orm_values():
    source = read("app/api/staff_ui_guards.py")

    assert "def _payment_code_value(value) -> str:" in source
    assert 'getattr(value, "value", None)' in source
    assert "_payment_code_value(payment.payment_code)" in source


def test_integrity_screen_is_personal_admin_only_and_read_only():
    source = read("app/api/staff_ui_guards.py")

    api = source.split('@router.get("/admin/payment-obligations/integrity")', 1)[1].split(
        '@router.get("/admin/payment-obligations/ui"', 1
    )[0]
    ui = source.split('@router.get("/admin/payment-obligations/ui"', 1)[1]
    assert "await _admin(request, db, x_admin_token)" in api
    assert "await _admin(request, db, x_admin_token)" in ui
    assert "create_payment" not in api
    assert "change_status" not in api
    assert "create_payment" not in ui
    assert "change_status" not in ui


def test_payment_review_has_direct_link_to_obligation_integrity_and_workdesk_deeplinks():
    source = read("app/api/staff_ui_guards.py")

    assert "/admin/payment-obligations/ui" in source
    assert "Проверить обязательства 30 000 / 70 000 ₽" in source
    assert 'f"/admin/workdesk/ui?case_id={int(case.id)}"' in source
    assert "_payment_integrity_link(PAYMENT_REVIEW_CENTER_HTML)" in source
