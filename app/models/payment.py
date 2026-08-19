from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    payment_code: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(10), default="RUB")
    status: Mapped[str] = mapped_column(String(100), default="PENDING", index=True)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payment_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    reservation_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    # These timestamps are business facts, not presentation metadata. updated_at
    # changes during review/refund/reconciliation and therefore cannot safely be
    # used as the date money was received or returned.
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refunded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    expired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    case = relationship("Case", back_populates="payments")


_RECEIVED_MONEY_STATUSES = frozenset(
    {
        "PAID",
        "PAID_REVIEW",
        "REFUND_PENDING",
        "REFUND_DECLINED",
        "REFUNDED",
    }
)
_LIFECYCLE_TIMESTAMP_FIELDS = {
    "FAILED": "failed_at",
    "CANCELLED": "cancelled_at",
    "REFUNDED": "refunded_at",
    "EXPIRED": "expired_at",
}


def _status_value(value: object) -> str:
    """Normalize plain strings and str-backed enums without importing domain code."""

    raw = getattr(value, "value", value)
    return str(raw or "").strip().upper()


@event.listens_for(Payment.status, "set", active_history=True)
def _stamp_payment_lifecycle_fact(
    target: Payment,
    value: object,
    oldvalue: object,
    _initiator,
) -> None:
    """Persist payment business timestamps at the model invariant boundary.

    Payment status can currently be changed by several domain services (provider
    webhook handling, review/reconciliation, cancellation/refund, stale-link
    expiry). The timestamp contract must therefore not depend on every caller
    remembering to set a second field. This listener is the database-model
    backstop: once a money/lifecycle fact is observed, its first timestamp is
    immutable unless a migration/controlled repair explicitly changes it.

    Services may set a provider-supplied exact timestamp before changing status;
    in that case the listener preserves that value instead of overwriting it.
    """

    new_status = _status_value(value)
    previous_status = _status_value(oldvalue)
    if not new_status or new_status == previous_status:
        return

    now = datetime.now(timezone.utc)
    if new_status in _RECEIVED_MONEY_STATUSES and target.paid_at is None:
        target.paid_at = now

    timestamp_field = _LIFECYCLE_TIMESTAMP_FIELDS.get(new_status)
    if timestamp_field and getattr(target, timestamp_field, None) is None:
        setattr(target, timestamp_field, now)
