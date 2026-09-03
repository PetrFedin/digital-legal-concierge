from __future__ import annotations

import inspect

from app.domain.notifications.notification_rules import NOTIFICATION_RULES
from app.domain.notifications.notification_templates import TEMPLATES
from app.domain.payments.payment_webhook_service import PaymentWebhookService


def test_successful_m1_payments_emit_next_step_notifications():
    source = inspect.getsource(PaymentWebhookService._emit_m1_paid_next_step)
    assert "M1_INITIAL_PAYMENT_CONFIRMED" in source
    assert "M1_COURT_PAYMENT_CONFIRMED" in source
    assert "M1_POWER_OF_ATTORNEY" in source
    assert "M1_ENFORCEMENT" in source


def test_failed_payment_notifies_client_without_advancing_case():
    source = inspect.getsource(PaymentWebhookService.process_failed_payment)
    assert "PAYMENT_FAILED_CLIENT" in source
    assert "change_status" not in source


def test_payment_notification_copy_has_explicit_next_actions():
    assert NOTIFICATION_RULES["M1_INITIAL_PAYMENT_CONFIRMED"]["recipients"] == [
        "client",
        "admin",
    ]
    assert "доверенность" in TEMPLATES["m1_initial_payment_confirmed"].lower()
    assert "исполнитель" in TEMPLATES["m1_court_payment_confirmed"].lower()
    assert "не подтверждён" in TEMPLATES["payment_failed_client"].lower()
    assert "моё дело" in TEMPLATES["payment_failed_client"].lower()
