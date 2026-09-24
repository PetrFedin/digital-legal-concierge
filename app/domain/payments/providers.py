from __future__ import annotations

import base64
import hashlib
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


def payment_idempotence_key(payment_id: int) -> str:
    """Stable 64-char provider key for one internal Payment across retries."""

    raw = f"digital-legal-concierge:{settings.app_env}:payment:{int(payment_id)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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

    async def retrieve_payment(self, provider_payment_id: str) -> dict:
        raise NotImplementedError


class DisabledPaymentProvider(BasePaymentProvider):
    async def create_payment(
        self,
        *,
        payment_id: int,
        amount: Decimal,
        currency: str,
        title: str,
        metadata: dict,
    ) -> PaymentProviderResult:
        raise RuntimeError(
            "Онлайн-оплата временно отключена. Свяжитесь с администратором."
        )

    async def retrieve_payment(self, provider_payment_id: str) -> dict:
        raise RuntimeError("Онлайн-оплата временно отключена")



class OfflinePaymentProvider(BasePaymentProvider):
    """Explicit manual bank/offline reconciliation mode.

    No external payment link is created. The obligation remains in the database
    and can become paid only through the authenticated administrator confirmation
    path after independent bank/accounting verification.
    """

    async def create_payment(
        self,
        *,
        payment_id: int,
        amount: Decimal,
        currency: str,
        title: str,
        metadata: dict,
    ) -> PaymentProviderResult:
        return PaymentProviderResult(
            provider="offline",
            provider_payment_id=f"offline-{payment_id}",
            payment_url="",
            raw={"mode": "offline", "metadata": metadata},
        )

    async def retrieve_payment(self, provider_payment_id: str) -> dict:
        raise RuntimeError(
            "Офлайн-платёж подтверждается администратором после проверки поступления"
        )


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
        provider_payment_id = f"fake-{payment_idempotence_key(payment_id)[:32]}"
        return PaymentProviderResult(
            provider="fake",
            provider_payment_id=provider_payment_id,
            payment_url=(
                f"{settings.public_base_url.rstrip('/')}"
                f"/webhooks/payments/fake-pay/{payment_id}"
            ),
            raw={"mode": "fake", "metadata": metadata},
        )

    async def retrieve_payment(self, provider_payment_id: str) -> dict:
        raise RuntimeError("Fake-провайдер не поддерживает удалённую проверку платежа")


class YooKassaPaymentProvider(BasePaymentProvider):
    """Минимальная интеграция YooKassa для production-режима."""

    api_url = "https://api.yookassa.ru/v3/payments"

    def _authorization_header(self) -> str:
        if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
            raise RuntimeError(
                "YooKassa не настроена: заполните YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY"
            )
        auth_raw = (
            f"{settings.yookassa_shop_id}:{settings.yookassa_secret_key}"
        ).encode("utf-8")
        return f"Basic {base64.b64encode(auth_raw).decode('ascii')}"

    async def create_payment(
        self,
        *,
        payment_id: int,
        amount: Decimal,
        currency: str,
        title: str,
        metadata: dict,
    ) -> PaymentProviderResult:
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
            "metadata": {
                **metadata,
                "internal_payment_id": str(payment_id),
            },
        }
        headers = {
            "Authorization": self._authorization_header(),
            "Idempotence-Key": payment_idempotence_key(payment_id),
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                self.api_url,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

        provider_payment_id = str(data.get("id") or "").strip()
        confirmation = data.get("confirmation") or {}
        payment_url = str(confirmation.get("confirmation_url") or "").strip()
        if not provider_payment_id or not payment_url:
            raise RuntimeError(
                "Платёжный провайдер вернул неполный ответ: отсутствует идентификатор "
                "операции или ссылка подтверждения. Внутренний платёж не будет переведён "
                "в ожидание оплаты; повтор с тем же idempotency key безопасно восстановит операцию."
            )

        return PaymentProviderResult(
            provider="yookassa",
            provider_payment_id=provider_payment_id,
            payment_url=payment_url,
            raw=data,
        )

    async def retrieve_payment(self, provider_payment_id: str) -> dict:
        if not provider_payment_id:
            raise ValueError("provider_payment_id обязателен")
        headers = {
            "Authorization": self._authorization_header(),
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{self.api_url}/{provider_payment_id}",
                headers=headers,
            )
            response.raise_for_status()
            return response.json()


def get_payment_provider() -> BasePaymentProvider:
    provider = str(settings.payment_provider or "").strip().lower()
    if provider == "disabled":
        return DisabledPaymentProvider()
    if provider == "offline":
        return OfflinePaymentProvider()
    if provider == "yookassa":
        return YooKassaPaymentProvider()
    if provider == "fake":
        if settings.app_env not in {"local", "test"}:
            raise RuntimeError(
                "Fake-провайдер запрещён вне local/test. "
                "Настройте YooKassa или явно отключите онлайн-оплату."
            )
        return FakePaymentProvider()
    raise RuntimeError(
        f"Неизвестный платёжный провайдер: {provider or '<empty>'}. "
        "Поддерживаются disabled, offline, fake (только local/test) и yookassa."
    )
