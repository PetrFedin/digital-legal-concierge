from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.payment_safety_guard import (
    guarded_fake_payment_page,
    guarded_fake_payment_success,
    guarded_fake_payment_webhook,
    require_local_test_fake_endpoint,
)
from app.config import settings
from app.domain.payments.mode import payments_enabled
from app.domain.payments.providers import FakePaymentProvider, get_payment_provider
from app.main import create_app


def _first_endpoint(path: str, method: str):
    for route in create_app().routes:
        if route.path == path and method in (route.methods or set()):
            return route.endpoint
    return None


def test_fake_payment_routes_are_shadowed_by_production_guard():
    assert (
        _first_endpoint("/webhooks/payments/fake", "POST")
        is guarded_fake_payment_webhook
    )
    assert (
        _first_endpoint("/webhooks/payments/fake-pay/{payment_id}", "GET")
        is guarded_fake_payment_page
    )
    assert (
        _first_endpoint("/webhooks/payments/fake-pay/{payment_id}/success", "POST")
        is guarded_fake_payment_success
    )


def test_demo_mode_cannot_enable_fake_payments_in_production(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "payment_provider", "fake")
    monkeypatch.setattr(settings, "demo_mode", True)
    assert payments_enabled() is False

    with pytest.raises(RuntimeError, match="Fake-провайдер запрещён"):
        get_payment_provider()
    with pytest.raises(HTTPException) as error:
        require_local_test_fake_endpoint()
    assert error.value.status_code == 404


def test_fake_provider_remains_available_only_for_local_or_test(monkeypatch):
    monkeypatch.setattr(settings, "payment_provider", "fake")
    monkeypatch.setattr(settings, "demo_mode", False)
    for environment in ("local", "test"):
        monkeypatch.setattr(settings, "app_env", environment)
        assert payments_enabled() is True
        assert isinstance(get_payment_provider(), FakePaymentProvider)
        require_local_test_fake_endpoint()


def test_unknown_provider_never_falls_back_to_fake(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "payment_provider", "yookasaa-typo")
    assert payments_enabled() is False
    with pytest.raises(RuntimeError, match="Неизвестный платёжный провайдер"):
        get_payment_provider()
