from __future__ import annotations

from decimal import Decimal


# Client-approved commercial contract for PM-027. A later business change must
# be introduced as an explicit versioned product change, not by silently
# editing a runtime setting after an obligation has been offered.
SELF_FILING_PRICE_RUB = Decimal("15000")
SELF_FILING_SLA_BUSINESS_DAYS = 2

# Mailbox ownership verification before sensitive court documents can ever be
# queued to an address. These are security controls, not commercial promises.
SELF_FILING_EMAIL_VERIFICATION_TTL_MINUTES = 15
SELF_FILING_EMAIL_VERIFICATION_MAX_ATTEMPTS = 5
SELF_FILING_EMAIL_VERIFICATION_PBKDF2_ROUNDS = 120_000


__all__ = [
    "SELF_FILING_PRICE_RUB",
    "SELF_FILING_SLA_BUSINESS_DAYS",
    "SELF_FILING_EMAIL_VERIFICATION_TTL_MINUTES",
    "SELF_FILING_EMAIL_VERIFICATION_MAX_ATTEMPTS",
    "SELF_FILING_EMAIL_VERIFICATION_PBKDF2_ROUNDS",
]
