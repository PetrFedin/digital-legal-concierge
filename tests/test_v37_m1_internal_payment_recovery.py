from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_recovery_mapping_has_only_atomic_paid_transient_stages():
    source = read("app/domain/cases/m1_internal_payment_recovery.py")
    assert "CaseStatus.M1_PAYMENT_30000_RECEIVED" in source
    assert "PaymentCode.M1_INITIAL_PAYMENT" in source
    assert "CaseStatus.M1_POWER_OF_ATTORNEY" in source
    assert "CaseStatus.M1_PAYMENT_70000_RECEIVED" in source
    assert "PaymentCode.M1_COURT_PAYMENT" in source
    assert "CaseStatus.M1_ENFORCEMENT" in source
    assert "CaseStatus.M1_SUCCESS_FEE_RECEIVED" in source
    assert "PaymentCode.M1_SUCCESS_FEE" in source
    assert "CaseStatus.M1_CLOSED" in source
    mapping = source.split("RECOVERY_PLANS:", 1)[1].split("class M1InternalPaymentRecoveryService", 1)[0]
    assert "M1_MONEY_RECEIVED" not in mapping


def test_recovery_requires_exact_paid_payment_and_row_locks():
    source = read("app/domain/cases/m1_internal_payment_recovery.py")
    assert "Payment.status == PaymentStatus.PAID" in source
    assert "Payment.payment_code == code" in source
    assert ".with_for_update()" in source
    assert "next_status=plan.target" in source
    assert "force=True" not in source
    assert 'action="M1_INTERNAL_PAYMENT_STAGE_RECOVERED"' in source


def test_admin_recovery_api_never_accepts_target_status_from_browser():
    source = read("app/api/m1_internal_payment_recovery.py")
    post = source.split('@router.post("/admin/workdesk/cases/{case_id}/recover-payment-stage")', 1)[1]
    assert "payload.get(\"comment\")" in post
    assert "payload.get(\"target\")" not in post
    assert "payload.get(\"status\")" not in post
    assert "admin_id=int(actor.account_id)" in post
    assert "resolve_document_actor" in source


def test_recovery_ui_shows_evidence_before_confirmation():
    source = read("app/api/m1_internal_payment_recovery.py")
    assert "Только доказанный PAID-платёж" in source
    assert "payment_id" in source
    assert "payment_status" in source
    assert "target_status_label" in source
    assert "Сервер ещё раз проверит текущий статус и PAID-платёж" in source
