from enum import StrEnum


class PaymentProcessingOutcome(StrEnum):
    """Persisted result of applying a successful payment to domain state."""

    # Payment and all related domain changes were applied successfully.
    PROCESSED = "processed"

    # Payment was confirmed, but an operator must complete processing.
    MANUAL_REVIEW_REQUIRED = "manual_review_required"

    # Contradictory persisted state prevents automatic processing.
    CONFLICT = "conflict"


# PaymentService will enforce this persisted contract when it starts writing outcomes:
# manual_review_required is true exactly for these outcomes and false for PROCESSED.
MANUAL_REVIEW_OUTCOMES: frozenset[PaymentProcessingOutcome] = frozenset(
    {
        PaymentProcessingOutcome.MANUAL_REVIEW_REQUIRED,
        PaymentProcessingOutcome.CONFLICT,
    }
)
