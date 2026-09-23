from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from app.config import settings
from app.domain.payments.mode import (
    payment_mode_valid,
    payment_provider_is_disabled,
    payment_provider_name,
    payments_disabled,
    payments_offline,
)
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.providers import (
    DisabledPaymentProvider,
    OfflinePaymentProvider,
    get_payment_provider,
)


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


def test_core_payment_link_creation_never_auto_marks_money_received():
    source = inspect.getsource(PaymentService.create_payment_link)

    # The canonical core path always asks the configured provider for a payment
    # and may only move the projection to WAITING_CONFIRMATION. Local/test
    # no-payment behaviour is handled outside this money-receipt boundary; a
    # disabled provider in staging/production therefore fails closed rather than
    # manufacturing a PAID fact.
    assert "provider.create_payment" in source
    assert "PaymentLifecycleService.transition" in source
    assert "PaymentStatus.WAITING_CONFIRMATION" in source
    assert "PaymentStatus.PAID" not in source
    assert "payments_disabled()" not in source

    mode_source = inspect.getsource(payments_disabled)
    assert "LOCAL_PAYMENT_BYPASS_ENVS" in mode_source
    assert "payment_provider_is_disabled()" in mode_source


def test_demo_mode_never_reenables_disabled_payment_bypass_in_production(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "payment_provider", "disabled")
    monkeypatch.setattr(settings, "demo_mode", True)

    assert payments_disabled() is False
    assert payment_mode_valid() is False



def test_offline_provider_is_valid_manual_reconciliation_mode(monkeypatch):
    monkeypatch.setattr(settings, "payment_provider", "offline")

    for environment in ("production", "staging"):
        monkeypatch.setattr(settings, "app_env", environment)
        assert payment_provider_name() == "offline"
        assert payments_offline() is True
        assert payments_disabled() is False
        assert payment_mode_valid() is True
        assert isinstance(get_payment_provider(), OfflinePaymentProvider)



@pytest.mark.asyncio
async def test_offline_provider_creates_no_external_payment_link(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "payment_provider", "offline")
    provider = get_payment_provider()

    result = await provider.create_payment(
        payment_id=42,
        amount=Decimal("30000.00"),
        currency="RUB",
        title="Первый платёж М1",
        metadata={"case_id": 7},
    )

    assert result.provider == "offline"
    assert result.provider_payment_id == "offline-42"
    assert result.payment_url == ""
    assert result.raw["mode"] == "offline"
