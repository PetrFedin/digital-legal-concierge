from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, event, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PaymentEventIntegrityError(RuntimeError):
    """Raised when application code tries to mutate the financial event ledger."""


class PaymentEvent(Base):
    """Append-only normalized ledger of persisted Payment lifecycle transitions."""

    __tablename__ = "payment_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("payments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status_before: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status_after: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    payment_code: Mapped[str] = mapped_column(String(100), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reservation_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="payment_model",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )


def _reject_payment_event_mutation(_mapper, _connection, _target: PaymentEvent) -> None:
    """Protect the append-only ledger from ordinary ORM update/delete paths.

    PaymentEvent rows are evidence of persisted financial state transitions.
    Corrections must be represented by a later Payment transition/event rather
    than rewriting or deleting historical evidence. Database-level permissions
    remain a separate deployment boundary; this listener is the application ORM
    invariant and mirrors the fail-closed AuditLog contract.
    """

    raise PaymentEventIntegrityError(
        "Записи финансового журнала неизменяемы. "
        "Исправление должно быть новым финансовым событием."
    )


# Registration is intentionally colocated with the model so every application
# Session gets the invariant, including background jobs and repair/admin flows.
event.listen(PaymentEvent, "before_update", _reject_payment_event_mutation)
event.listen(PaymentEvent, "before_delete", _reject_payment_event_mutation)


__all__ = ["PaymentEvent", "PaymentEventIntegrityError"]
