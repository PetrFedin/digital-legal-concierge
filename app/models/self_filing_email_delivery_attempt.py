from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SelfFilingEmailDeliveryAttempt(Base, TimestampMixin):
    """Durable authority for one SMTP delivery attempt.

    The row is committed before SMTP I/O. A crash after SMTP acceptance therefore
    leaves SENDING evidence that is reconciled to UNKNOWN rather than blindly
    resent. The four attachment ids/hashes and recipient are frozen per attempt.
    """

    __tablename__ = "self_filing_email_delivery_attempts"
    __table_args__ = (
        UniqueConstraint(
            "package_id",
            "attempt_number",
            name="uq_self_filing_email_attempt_package_number",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(
        ForeignKey("self_filing_packages.id"),
        nullable=False,
        index=True,
    )
    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id"),
        nullable=False,
        index=True,
    )
    package_version: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False)
    message_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    documents_snapshot: Mapped[list[dict]] = mapped_column(JSON, nullable=False)

    sending_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    unknown_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_receipt: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = ["SelfFilingEmailDeliveryAttempt"]
