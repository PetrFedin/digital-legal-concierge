from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_lawyer_court_decision_opens_exact_second_payment_state():
    service = read("app/domain/cases/m1_claim_service.py")
    api = read("app/api/lawyer_m1_claim.py")

    assert "async def open_court_payment(" in service
    assert "if self._status(case) != CaseStatus.M1_COURT_STAGE" in service
    assert "next_status=CaseStatus.M1_WAITING_PAYMENT_70000" in service
    assert '@router.post("/cases/{case_id}/court/payment/open")' in api
    assert 'event_code="COURT_PAYMENT_OPENED"' in api


def test_client_notification_lands_on_existing_court_payment_screen():
    actions = read("app/domain/notifications/notification_actions.py")
    stages = read("app/bot/screens/m1_stages.py")

    assert 'if event_code == "COURT_PAYMENT_OPENED":' in actions
    assert '("💳 Перейти к оплате 70 000 ₽", "court_status")' in actions
    assert "CaseStatus.M1_WAITING_PAYMENT_70000" in stages
    assert 'bound_case_callback("pay_court_70000", case.id)' in stages


def test_client_court_payment_callback_creates_only_existing_court_payment_code():
    stages = read("app/bot/screens/m1_stages.py")
    start = stages.index('callback_matches_action(c.data, "pay_court_70000")')
    end = stages.index('callback_matches_action(c.data, "pay_success_fee")')
    block = stages[start:end]

    assert "CaseStatus.M1_WAITING_PAYMENT_70000" in block
    assert "payments_disabled()" in block
    assert "PaymentCode.M1_COURT_PAYMENT" in block
    assert "scope=scope" in block
    assert "change_status(" not in block


def test_successful_court_payment_webhook_advances_to_enforcement():
    webhook = read("app/domain/payments/payment_webhook_service.py")

    assert "PaymentCode.M1_COURT_PAYMENT" in webhook
    transition_mapping = webhook[webhook.index("mapping = {") :]
    court_mapping = transition_mapping[
        transition_mapping.index("PaymentCode.M1_COURT_PAYMENT") :
        transition_mapping.index("PaymentCode.M1_SUCCESS_FEE")
    ]
    assert "CaseStatus.M1_PAYMENT_70000_RECEIVED" in court_mapping
    assert "CaseStatus.M1_ENFORCEMENT" in court_mapping


def test_court_payment_handoff_has_no_generic_status_endpoint():
    api = read("app/api/lawyer_m1_claim.py")
    workspace = read("app/api/lawyer_workspace.py")

    assert 'router.post("/cases/{case_id}/status")' not in api
    assert "`/lawyer/cases/${id}/court/payment/open`" in workspace
    assert "open_court_payment" in workspace
