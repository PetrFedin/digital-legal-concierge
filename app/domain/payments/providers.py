from __future__ import annotations

import base64
from dataclasses import dataclass
from decimal import Decimal

import httpx

from app.config import settings


@dataclass
class PaymentProviderResult:
    provider: str
    provider_payment_id: str
    payment_url: str
    raw: dict | None = None


class BasePaymentProvider:
    async def create_payment(
        self,
        *,
        payment_id: int,
        amount: Decimal,
        currency: str,
        title: str,
        metadata: dict,
    ) -> PaymentProviderResult:
        raise NotImplementedError


class FakePaymentProvider(BasePaymentProvider):
    async def create_payment(
        self,
        *,
        payment_id: int,
        amount: Decimal,
        currency: str,
        title: str,
        metadata: dict,
    ) -> PaymentProviderResult:
        # Stable for the same internal Payment, matching the production
        # idempotency contract instead of generating a new external operation
        # on every retry.
        provider_payment_id = f"fake-payment-{payment_id}"
        return PaymentProviderResult(
            provider="fake",
            provider_payment_id=provider_payment_id,
            payment_url=(
                f"{settings.public_base_url.rstrip('/')}"
                f"/webhooks/payments/fake-pay/{payment_id}"
            ),
            raw={
                "mode": "fake",
                "metadata": metadata,
                "idempotency_key": _idempotency_key(payment_id),
            },
        )


class YooKassaPaymentProvider(BasePaymentProvider):
    """Minimal YooKassa integration with stable request idempotency."""

    api_url = "https://api.yookassa.ru/v3/payments"

    async def create_payment(
        self,
        *,
        payment_id: int,
        amount: Decimal,
        currency: str,
        title: str,
        metadata: dict,
    ) -> PaymentProviderResult:
        if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
            raise RuntimeError(
                "YooKassa не настроена: заполните YOOKASSA_SHOP_ID "
                "и YOOKASSA_SECRET_KEY"
            )

        auth_raw = (
            f"{settings.yookassa_shop_id}:{settings.yookassa_secret_key}"
        ).encode("utf-8")
        auth_header = base64.b64encode(auth_raw).decode("ascii")
        payload = {
            "amount": {"value": f"{amount:.2f}", "currency": currency},
            "capture": True,
            "confirmation": {
                "type": "redirect",
                "return_url": (
                    f"{settings.public_base_url.rstrip('/')}"
                    f"/payment-result?payment_id={payment_id}"
                ),
            },
            "description": title[:128],
            "metadata": {**metadata, "internal_payment_id": str(payment_id)},
        }
        headers = {
            "Authorization": f"Basic {auth_header}",
            "Idempotence-Key": _idempotency_key(payment_id),
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(self.api_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        confirmation = data.get("confirmation") or {}
        provider_payment_id = str(data.get("id") or "").strip()
        payment_url = str(confirmation.get("confirmation_url") or "").strip()
        if not provider_payment_id or not payment_url:
            raise RuntimeError(
                "YooKassa вернула неполный ответ без идентификатора или ссылки."
            )
        return PaymentProviderResult(
            provider="yookassa",
            provider_payment_id=provider_payment_id,
            payment_url=payment_url,
            raw=data,
        )


def _idempotency_key(payment_id: int) -> str:
    if payment_id <= 0:
        raise ValueError("Для платёжного запроса требуется сохранённый Payment ID.")
    return f"digital-legal-concierge-payment-{payment_id}"


def get_payment_provider() -> BasePaymentProvider:
    if settings.payment_provider == "yookassa":
        return YooKassaPaymentProvider()
    return FakePaymentProvider()
