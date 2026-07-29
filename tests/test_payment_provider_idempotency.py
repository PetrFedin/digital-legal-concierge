from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.domain.payments import payment_service as payment_service_module
from app.domain.payments.payment_service import PaymentIntegrityError, PaymentService
from app.domain.payments.providers import FakePaymentProvider, _idempotency_key
from app.domain.statuses.payment_statuses import PaymentStatus


@pytest.mark.asyncio
async def test_fake_provider_is_stable_for_same_internal_payment():
    provider = FakePaymentProvider()

    first = await provider.create_payment(
        payment_id=42,
        amount=Decimal("5000.00"),
        currency="RUB",
        title="Оплата консультации",
        metadata={"case_id": 10},
    )
    second = await provider.create_payment(
        payment_id=42,
        amount=Decimal("5000.00"),
        currency="RUB",
        title="Оплата консультации",
        metadata={"case_id": 10},
    )

    assert first.provider_payment_id == second.provider_payment_id
    assert first.payment_url == second.payment_url
    assert first.raw["idempotency_key"] == _idempotency_key(42)
    assert _idempotency_key(42) == "digital-legal-concierge-payment-42"


def test_idempotency_key_requires_persisted_payment_id():
    with pytest.raises(ValueError, match="Payment ID"):
        _idempotency_key(0)


@pytest.mark.asyncio
async def test_incomplete_provider_response_does_not_mutate_payment(monkeypatch):
    class FakeDb:
        def __init__(self):
            self.flushes = 0

        async def flush(self):
            self.flushes += 1

    class IncompleteProvider:
        async def create_payment(self, **kwargs):
            return SimpleNamespace(
                provider="custom",
                provider_payment_id="",
                payment_url="",
            )

    monkeypatch.setattr(
        payment_service_module,
        "get_payment_provider",
        lambda: IncompleteProvider(),
    )
    payment = SimpleNamespace(
        id=55,
        case_id=9,
        payment_code="M2_CONSULTATION_PAYMENT",
        amount=Decimal("5000.00"),
        currency="RUB",
        title="Оплата консультации",
        status=PaymentStatus.PENDING.value,
        provider=None,
        provider_payment_id=None,
        payment_url=None,
    )
    db = FakeDb()

    with pytest.raises(PaymentIntegrityError, match="неполный ответ"):
        await PaymentService(db).create_payment_link(payment)

    assert payment.status == PaymentStatus.PENDING.value
    assert payment.provider is None
    assert payment.provider_payment_id is None
    assert payment.payment_url is None
    assert db.flushes == 0
