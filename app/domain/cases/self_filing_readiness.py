from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.domain.cases.self_filing_business_calendar import (
    BusinessCalendarError,
    add_business_days,
    load_business_calendar,
)
from app.domain.cases.self_filing_email_sender import (
    email_delivery_configuration_error,
)
from app.system.settings_service import SettingsService


SELF_FILING_CONTRACT_PRICE = Decimal("15000")
SELF_FILING_CONTRACT_SLA_BUSINESS_DAYS = 2


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
            await settings_service.get_value("self_filing.sla_business_days")
        )
    except Exception:
        configured_days = -1

    email_error = email_delivery_configuration_error()
    email_configuration_ready = email_error is None

    calendar_ready = False
    calendar_error: str | None = None
    coverage_through: str | None = None
    sample_due_at: str | None = None
    try:
        calendar = await load_business_calendar(db)
        coverage_through = calendar.coverage_through.isoformat()
        due = add_business_days(
            checked_at,
            business_days=configured_days,
            calendar=calendar,
        )
        sample_due_at = due.isoformat()
        calendar_ready = True
    except (BusinessCalendarError, KeyError, TypeError, ValueError) as error:
        calendar_error = str(error)

    price_matches_contract = configured_price == SELF_FILING_CONTRACT_PRICE
    sla_matches_contract = (
        configured_days == SELF_FILING_CONTRACT_SLA_BUSINESS_DAYS
    )
    configuration_ready = bool(
        email_configuration_ready
        and calendar_ready
        and price_matches_contract
        and sla_matches_contract
    )

    blockers: list[str] = []
    if not price_matches_contract:
        blockers.append(
            "Стоимость услуги отличается от согласованных 15 000 ₽"
        )
    if not sla_matches_contract:
        blockers.append(
            "SLA услуги отличается от согласованных 2 рабочих дней"
        )
    if not calendar_ready:
        blockers.append(calendar_error or "Рабочий календарь не готов")
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
        "configured_sla_business_days": configured_days,
        "contract_sla_business_days": SELF_FILING_CONTRACT_SLA_BUSINESS_DAYS,
        "sla_matches_contract": sla_matches_contract,
        "email_configuration_ready": email_configuration_ready,
        "email_configuration_error": email_error,
        "calendar_ready": calendar_ready,
        "calendar_error": calendar_error,
        "calendar_coverage_through": coverage_through,
        "sample_sla_due_at": sample_due_at,
        "configuration_ready_for_controlled_acceptance": configuration_ready,
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
    "SELF_FILING_CONTRACT_SLA_BUSINESS_DAYS",
    "self_filing_readiness",
]
