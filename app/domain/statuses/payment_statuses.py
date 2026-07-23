from __future__ import annotations

from enum import StrEnum


class PaymentStatus(StrEnum):
    PENDING = "PENDING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    PAID = "PAID"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"
    EXPIRED = "EXPIRED"


PAID_PAYMENT_STATUSES = frozenset({PaymentStatus.PAID.value})
REFUNDED_PAYMENT_STATUSES = frozenset({PaymentStatus.REFUNDED.value})
OPEN_PAYMENT_STATUSES = frozenset(
    {
        PaymentStatus.PENDING.value,
        PaymentStatus.WAITING_CONFIRMATION.value,
    }
)
FAILED_PAYMENT_STATUSES = frozenset(
    {
        PaymentStatus.FAILED.value,
        PaymentStatus.CANCELLED.value,
        PaymentStatus.EXPIRED.value,
    }
)
TERMINAL_PAYMENT_STATUSES = frozenset(
    PAID_PAYMENT_STATUSES | REFUNDED_PAYMENT_STATUSES | FAILED_PAYMENT_STATUSES
)


def normalize_payment_status(status: str | PaymentStatus | None) -> str:
    if isinstance(status, PaymentStatus):
        return status.value
    return str(status or "").strip().upper()


def is_open_payment_status(status: str | PaymentStatus | None) -> bool:
    return normalize_payment_status(status) in OPEN_PAYMENT_STATUSES


def is_terminal_payment_status(status: str | PaymentStatus | None) -> bool:
    return normalize_payment_status(status) in TERMINAL_PAYMENT_STATUSES


def is_successful_payment_status(status: str | PaymentStatus | None) -> bool:
    return normalize_payment_status(status) in PAID_PAYMENT_STATUSES
