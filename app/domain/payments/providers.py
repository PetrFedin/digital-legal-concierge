from __future__ import annotations

import base64
from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

import httpx

from app.config import settings


@dataclass
class PaymentProviderResult:
    provider: str
    provider_payment_id: str
    payment_url: str
    raw: dict | None = None


class BasePaymentProvider:
    async def create_payment(self, *, payment_id: int, amount: Decimal, currency: str, title: str, metadata: dict) -> PaymentProviderResult:
        raise NotImplementedError


class FakePaymentProvider(BasePaymentProvider):
    async def create_payment(self, *, payment_id: int, amount: Decimal, currency: str, title: str, metadata: dict) -> PaymentProviderResult:
        provider_payment_id = str(uuid4())
        return PaymentProviderResult(
            provider="fake",
            provider_payment_id=provider_payment_id,
            payment_url=f"{settings.public_base_url.rstrip('/')}/webhooks/payments/fake-pay/{payment_id}",
            raw={"mode": "fake", "metadata": metadata},
        )


class YooKassaPaymentProvider(BasePaymentProvider):
    """Минимальная интеграция YooKassa для production-режима.

    Нужны переменные окружения:
    - PAYMENT_PROVIDER=yookassa
    - YOOKASSA_SHOP_ID
    - YOOKASSA_SECRET_KEY
    - PUBLIC_BASE_URL
    """

    api_url = "https://api.yookassa.ru/v3/payments"

    async def create_payment(self, *, payment_id: int, amount: Decimal, currency: str, title: str, metadata: dict) -> PaymentProviderResult:
        if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
            raise RuntimeError("YooKassa не настроена: заполните YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY")

        auth_raw = f"{settings.yookassa_shop_id}:{settings.yookassa_secret_key}".encode("utf-8")
        auth_header = base64.b64encode(auth_raw).decode("ascii")
        idempotence_key = str(uuid4())
        payload = {
            "amount": {"value": f"{amount:.2f}", "currency": currency},
            "capture": True,
            "confirmation": {
                "type": "redirect",
                "return_url": f"{settings.public_base_url.rstrip('/')}/payment-result?payment_id={payment_id}",
            },
            "description": title[:128],
            "metadata": {**metadata, "internal_payment_id": str(payment_id)},
        }
        headers = {
            "Authorization": f"Basic {auth_header}",
            "Idempotence-Key": idempotence_key,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(self.api_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        confirmation = data.get("confirmation") or {}
        return PaymentProviderResult(
            provider="yookassa",
            provider_payment_id=data.get("id", ""),
            payment_url=confirmation.get("confirmation_url", ""),
            raw=data,
        )


def get_payment_provider() -> BasePaymentProvider:
    if settings.payment_provider == "yookassa":
        return YooKassaPaymentProvider()
    return FakePaymentProvider()
