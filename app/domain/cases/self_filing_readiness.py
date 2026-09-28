from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.config import settings
from app.domain.cases.self_filing_contract import (
    SELF_FILING_PRICE_RUB,
    SELF_FILING_DELIVERY_CALENDAR_DAYS,
)
from app.domain.cases.self_filing_email_sender import (
    email_delivery_configuration_error,
)
from app.system.settings_service import SettingsService


# Compatibility aliases for existing readiness callers/tests.
SELF_FILING_CONTRACT_PRICE = SELF_FILING_PRICE_RUB
SELF_FILING_CONTRACT_DELIVERY_CALENDAR_DAYS = SELF_FILING_DELIVERY_CALENDAR_DAYS
# Compatibility alias for older diagnostics only.
SELF_FILING_CONTRACT_SLA_BUSINESS_DAYS = SELF_FILING_DELIVERY_CALENDAR_DAYS


async def self_filing_readiness(
    db,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Return operational preflight facts without claiming external SMTP delivery.

    email_configuration_ready proves only that the runtime contract is
    internally complete. A real provider send remains a separate production
    acceptance proof and is deliberately not synthesized here.
    """

    checked_at = now or datetime.now(timezone.utc)
    settings_service = SettingsService(db)

    try:
        configured_price = Decimal(
            str(
                await settings_service.get_value(
                    "payments.m1_self_filing_package"
                )
            )
        )
    except Exception:
        configured_price = Decimal("-1")

    try:
        configured_days = int(
            await settings_service.get_value("self_filing.delivery_calendar_days")
        )
    except Exception:
        configured_days = -1

    email_error = email_delivery_configuration_error()
    email_configuration_ready = email_error is None

    price_matches_contract = configured_price == SELF_FILING_CONTRACT_PRICE
    delivery_days_match_contract = (
        configured_days == SELF_FILING_CONTRACT_DELIVERY_CALENDAR_DAYS
    )
    payment_mode = str(settings.payment_provider or "").strip().lower()
    online_payment_configured = bool(
        payment_mode == "yookassa"
        and str(settings.yookassa_shop_id or "").strip()
        and str(settings.yookassa_secret_key or "").strip()
    )
    test_payment_configured = bool(
        settings.app_env in {"local", "test"} and payment_mode == "fake"
    )
    customer_payment_ready = online_payment_configured or test_payment_configured
    configuration_ready = bool(
        email_configuration_ready
        and price_matches_contract
        and delivery_days_match_contract
        and customer_payment_ready
    )
    new_sales_enabled = bool(settings.self_filing_new_sales_enabled)

    blockers: list[str] = []
    if not new_sales_enabled:
        blockers.append(
            "Новые продажи пакета самостоятельной подачи выключены до controlled activation"
        )
    if not price_matches_contract:
        blockers.append(
            "Стоимость услуги отличается от согласованных 15 000 ₽"
        )
    if not delivery_days_match_contract:
        blockers.append(
            "Срок выдачи отличается от согласованных 3 календарных дней после оплаты"
        )
    if not customer_payment_ready:
        blockers.append(
            "Клиентская оплата не готова: для production нужен настроенный YooKassa "
            "redirect (в local/test допустим fake). Offline-режим не даёт клиенту "
            "кнопку перехода в банк/платёжный сервис."
        )
    if not email_configuration_ready:
        blockers.append(
            "Email-конфигурация не готова: "
            + str(email_error or "неизвестная ошибка")
        )

    return {
        "checked_at": checked_at.isoformat(),
        "configured_price_rub": str(configured_price),
        "contract_price_rub": str(SELF_FILING_CONTRACT_PRICE),
        "price_matches_contract": price_matches_contract,
        "configured_delivery_calendar_days": configured_days,
        "contract_delivery_calendar_days": SELF_FILING_CONTRACT_DELIVERY_CALENDAR_DAYS,
        "delivery_days_match_contract": delivery_days_match_contract,
        "payment_mode": payment_mode,
        "customer_payment_ready": customer_payment_ready,
        "email_configuration_ready": email_configuration_ready,
        "email_configuration_error": email_error,
        "configuration_ready_for_controlled_acceptance": configuration_ready,
        "new_sales_enabled": new_sales_enabled,
        "external_email_delivery_verified": False,
        "production_ready": False,
        "remaining_external_proof": (
            "Выполнить контролируемую реальную SMTP-доставку и зафиксировать "
            "её Message-ID/время/получение до production activation."
        ),
        "blockers": blockers,
    }


__all__ = [
    "SELF_FILING_CONTRACT_PRICE",
    "SELF_FILING_CONTRACT_DELIVERY_CALENDAR_DAYS",
    "SELF_FILING_CONTRACT_SLA_BUSINESS_DAYS",
    "self_filing_readiness",
]
