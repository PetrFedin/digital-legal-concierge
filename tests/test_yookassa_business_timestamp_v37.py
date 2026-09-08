from __future__ import annotations

import inspect
from datetime import datetime, timezone

from app.api.payment_webhooks import parse_provider_datetime, yookassa_payment_webhook
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_webhook_service import PaymentWebhookService


def test_provider_datetime_is_normalized_to_utc_without_host_timezone_dependency():
    assert parse_provider_datetime("2026-08-19T12:15:30Z") == datetime(
        2026, 8, 19, 12, 15, 30, tzinfo=timezone.utc
    )
    assert parse_provider_datetime("2026-08-19T15:15:30+03:00") == datetime(
        2026, 8, 19, 12, 15, 30, tzinfo=timezone.utc
    )


def test_provider_datetime_missing_or_malformed_falls_back_in_lifecycle_not_parser():
    assert parse_provider_datetime(None) is None
    assert parse_provider_datetime("") is None
    assert parse_provider_datetime("not-a-provider-timestamp") is None


def test_yookassa_success_uses_captured_at_not_created_at_as_money_fact():
    source = inspect.getsource(yookassa_payment_webhook)

    assert 'verified.get("captured_at")' in source
    assert 'occurred_at=parse_provider_datetime(verified.get("captured_at"))' in source
    assert 'occurred_at=parse_provider_datetime(verified.get("created_at"))' not in source


def test_money_fact_time_is_carried_through_webhook_and_core_lifecycle_boundaries():
    webhook = inspect.getsource(PaymentWebhookService.process_successful_payment)
    mark_paid = inspect.getsource(PaymentService.mark_paid)

    assert "occurred_at: datetime | None = None" in webhook
    assert "occurred_at=occurred_at" in webhook
    assert "occurred_at: datetime | None = None" in mark_paid
    assert "PaymentLifecycleService.transition" in mark_paid
    assert "occurred_at=occurred_at" in mark_paid
