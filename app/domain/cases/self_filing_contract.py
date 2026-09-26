from __future__ import annotations

from decimal import Decimal


# Client-approved commercial contract for PM-027. A later business change must
# be introduced as an explicit versioned product change, not by silently
# editing a runtime setting after an obligation has been offered.
SELF_FILING_PRICE_RUB = Decimal("15000")
SELF_FILING_SLA_BUSINESS_DAYS = 2


__all__ = [
    "SELF_FILING_PRICE_RUB",
    "SELF_FILING_SLA_BUSINESS_DAYS",
]
