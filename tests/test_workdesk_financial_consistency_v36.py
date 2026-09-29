from pathlib import Path


WORKDESK_SOURCE = Path("app/api/workdesk.py").read_text(encoding="utf-8")
WORKDESK_UI_SOURCE = Path("app/api/workdesk_ui.py").read_text(encoding="utf-8")


def test_financial_reason_priority_is_above_operational_reasons():
    start = WORKDESK_SOURCE.index("_REASON_PRIORITY =")
    end = WORKDESK_SOURCE.index("_REASON_LABELS =", start)
    block = WORKDESK_SOURCE[start:end]
    assert '"payment_review": 0' in block
    assert '"refund": 1' in block
    assert '"overdue": 2' in block
    assert '"messages": 3' in block
    assert '"unassigned": 4' in block


def test_financial_primary_action_keeps_exact_payment_and_case_context():
    start = WORKDESK_SOURCE.index("def _primary_action(")
    end = WORKDESK_SOURCE.index("\n\ndef _attention_item(", start)
    block = WORKDESK_SOURCE[start:end]
    assert 'payment_id = int(review_ids[0])' in block
    assert 'payment_id = int(refund_ids[0])' in block
    assert 'payment_id={payment_id}&case_id={case.id}' in block


def test_workdesk_ui_exposes_client_readable_financial_labels():
    assert "Получено — требуется сверка" in WORKDESK_UI_SOURCE
    assert "Возврат обрабатывается" in WORKDESK_UI_SOURCE
    assert "/admin/payment-reviews/ui?payment_id=" in WORKDESK_UI_SOURCE
    assert "/admin/refunds/ui?payment_id=" in WORKDESK_UI_SOURCE


def test_integrity_center_treats_self_filing_closed_as_terminal():
    source = open("app/api/workdesk_integrity.py", encoding="utf-8").read()
    terminal = source.split("TERMINAL_CASE_STATUSES = {", 1)[1].split("}", 1)[0]
    assert "CaseStatus.M1_SELF_FILING_CLOSED.value" in terminal
