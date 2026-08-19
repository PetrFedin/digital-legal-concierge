from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.payment import Payment


_RECEIVED_MONEY_STATUSES = frozenset(
    {
        PaymentStatus.PAID,
        PaymentStatus.PAID_REVIEW,
        PaymentStatus.REFUND_PENDING,
        PaymentStatus.REFUND_DECLINED,
        PaymentStatus.REFUNDED,
    }
)
_LIFECYCLE_TIMESTAMP_FIELDS = {
    PaymentStatus.FAILED: "failed_at",
    PaymentStatus.CANCELLED: "cancelled_at",
    PaymentStatus.REFUNDED: "refunded_at",
    PaymentStatus.EXPIRED: "expired_at",
}


@dataclass(frozen=True)
class PaymentTransition:
    old_status: PaymentStatus
    new_status: PaymentStatus
    changed: bool
    occurred_at: datetime


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _status(value: object) -> PaymentStatus:
    raw = getattr(value, "value", value)
    return PaymentStatus(str(raw).strip().upper())


class PaymentLifecycleService:
    """Single application boundary for persisted Payment status changes.

    Domain services remain responsible for deciding whether a transition is
    legally/business-wise allowed and for writing their Case/Audit history. This
    class owns the financial projection itself: normalized status plus the first
    immutable timestamp of the corresponding money/lifecycle fact.

    The SQLAlchemy model listener remains as a final invariant backstop for
    migrations/controlled repairs and older code, but product code should use
    this service instead of assigning ``payment.status`` directly.
    """

    @staticmethod
    def transition(
        payment: Payment,
        *,
        to_status: PaymentStatus | str,
        occurred_at: datetime | None = None,
    ) -> PaymentTransition:
        old_status = _status(payment.status)
        new_status = _status(to_status)
        at = _as_utc(occurred_at)

        if old_status == new_status:
            return PaymentTransition(
                old_status=old_status,
                new_status=new_status,
                changed=False,
                occurred_at=at,
            )

        # Set exact provider/business timestamps before status assignment. The
        # model-level listener then sees the timestamp and deliberately preserves
        # it instead of replacing it with its own clock value.
        if new_status in _RECEIVED_MONEY_STATUSES and payment.paid_at is None:
            payment.paid_at = at

        timestamp_field = _LIFECYCLE_TIMESTAMP_FIELDS.get(new_status)
        if timestamp_field and getattr(payment, timestamp_field, None) is None:
            setattr(payment, timestamp_field, at)

        payment.status = new_status
        return PaymentTransition(
            old_status=old_status,
            new_status=new_status,
            changed=True,
            occurred_at=at,
        )


__all__ = ["PaymentLifecycleService", "PaymentTransition"]
