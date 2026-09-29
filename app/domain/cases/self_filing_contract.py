from __future__ import annotations

from decimal import Decimal


# Client-approved commercial contract for PM-027. A later business change must
# be introduced as an explicit versioned product change, not by silently
# editing a runtime setting after an obligation has been offered.
SELF_FILING_PRICE_RUB = Decimal("15000")
# Customer-approved delivery promise (27 Sep 2026): the completed result is
# delivered within three calendar days after money is actually received.
SELF_FILING_DELIVERY_CALENDAR_DAYS = 3

# Compatibility alias for callers not yet migrated. New business logic must use
# SELF_FILING_DELIVERY_CALENDAR_DAYS and must not interpret this as business days.
SELF_FILING_SLA_BUSINESS_DAYS = SELF_FILING_DELIVERY_CALENDAR_DAYS

# Mailbox ownership verification before sensitive court documents can ever be
# queued to an address. These are security controls, not commercial promises.
SELF_FILING_EMAIL_VERIFICATION_TTL_MINUTES = 15
SELF_FILING_EMAIL_VERIFICATION_MAX_ATTEMPTS = 5
SELF_FILING_EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS = 60
SELF_FILING_EMAIL_VERIFICATION_PBKDF2_ROUNDS = 120_000


__all__ = [
    "SELF_FILING_PRICE_RUB",
    "SELF_FILING_DELIVERY_CALENDAR_DAYS",
    "SELF_FILING_SLA_BUSINESS_DAYS",
    "SELF_FILING_EMAIL_VERIFICATION_TTL_MINUTES",
    "SELF_FILING_EMAIL_VERIFICATION_MAX_ATTEMPTS",
    "SELF_FILING_EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS",
    "SELF_FILING_EMAIL_VERIFICATION_PBKDF2_ROUNDS",
]
