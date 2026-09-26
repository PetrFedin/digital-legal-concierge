from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class SelfFilingPackage(Base, TimestampMixin):
    __tablename__ = "self_filing_packages"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id"),
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="PROFILE_PENDING",
        index=True,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )

    # Client-provided delivery/jurisdiction facts. The system deliberately does
    # not derive a court from these fields automatically.
    client_region: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    email_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    email_verification_salt: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    email_verification_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    email_verification_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    email_verification_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    email_verification_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    email_verification_message_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    documents_complete_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    documents_complete_by_lawyer_id: Mapped[int | None] = mapped_column(
        ForeignKey("lawyers.id"),
        nullable=True,
        index=True,
    )

    # Court/jurisdiction is a lawyer-confirmed legal fact, never an address
    # lookup guess.
    court_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    court_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    jurisdiction_basis: Mapped[str | None] = mapped_column(String(100), nullable=True)
    jurisdiction_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    jurisdiction_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    jurisdiction_confirmed_by_lawyer_id: Mapped[int | None] = mapped_column(
        ForeignKey("lawyers.id"),
        nullable=True,
        index=True,
    )

    payment_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    sla_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    sla_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    package_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id"),
        nullable=True,
        index=True,
    )
    ready_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    email_delivery_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="NOT_QUEUED",
        index=True,
    )
    email_delivery_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    email_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    email_last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    case = relationship("Case", back_populates="self_filing_package")
    package_document = relationship("Document", foreign_keys=[package_document_id])


__all__ = ["SelfFilingPackage"]
