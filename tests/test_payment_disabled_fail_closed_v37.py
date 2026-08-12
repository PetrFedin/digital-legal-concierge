from __future__ import annotations

import inspect

import pytest

from app.config import settings
from app.domain.payments.mode import (
    payment_mode_valid,
    payment_provider_is_disabled,
    payments_disabled,
)
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.providers import DisabledPaymentProvider, get_payment_provider


def test_disabled_provider_is_only_a_local_test_bypass(monkeypatch):
    monkeypatch.setattr(settings, "payment_provider", "disabled")
    monkeypatch.setattr(settings, "demo_mode", True)

    for environment in ("production", "staging"):
        monkeypatch.setattr(settings, "app_env", environment)
        assert payment_provider_is_disabled() is True
        assert payments_disabled() is False
        assert payment_mode_valid() is False
        assert isinstance(get_payment_provider(), DisabledPaymentProvider)

    for environment in ("local", "test"):
        monkeypatch.setattr(settings, "app_env", environment)
        assert payment_provider_is_disabled() is True
        assert payments_disabled() is True
        assert payment_mode_valid() is True


def test_payment_service_auto_paid_branch_is_guarded_by_local_test_only_function():
    source = inspect.getsource(PaymentService.create_payment)
    assert "if payments_disabled():" in source
    assert "payment.status = PaymentStatus.PAID" in source
    assert "process_successful_payment" in source

    # Production/staging can no longer enter the legacy branch above because
    # payments_disabled() is environment-gated. The provider path therefore
    # reaches DisabledPaymentProvider and fails before a paid event is recorded.
    mode_source = inspect.getsource(payments_disabled)
    assert "LOCAL_PAYMENT_BYPASS_ENVS" in mode_source
    assert "payment_provider_is_disabled()" in mode_source


def test_demo_mode_never_reenables_disabled_payment_bypass_in_production(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "payment_provider", "disabled")
    monkeypatch.setattr(settings, "demo_mode", True)

    assert payments_disabled() is False
    assert payment_mode_valid() is False
