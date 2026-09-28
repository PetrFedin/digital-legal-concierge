from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
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

    # Transfer-act fact is confirmed by the responsible lawyer at the same gate
    # that opens payment. This prevents a stale preliminary calculator answer
    # from deciding the customer-approved claim-calculation cutoff.
    transfer_act_signed: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )
    transfer_act_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        index=True,
    )
    transfer_act_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    transfer_act_confirmed_by_lawyer_id: Mapped[int | None] = mapped_column(
        ForeignKey("lawyers.id"),
        nullable=True,
        index=True,
    )

    # Immutable calculator snapshot used as source material for the lawyer-authored
    # "Расчёт суммы иска". It is not itself an automated legal conclusion.
    # Cutoff: confirmed transfer-act date if signed, otherwise service-payment date.
    claim_source_calculation_id: Mapped[int | None] = mapped_column(
        ForeignKey("calculations.id"),
        nullable=True,
        index=True,
    )
    claim_calculation_cutoff_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        index=True,
    )
    claim_calculation_basis: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    claim_update_in_court_required: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
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

    # Legacy single-file package pointer remains readable for already-created
    # rows. New work must use exactly the four customer-approved deliverables.
    package_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id"),
        nullable=True,
        index=True,
    )
    pretrial_claim_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id"),
        nullable=True,
        index=True,
    )
    statement_of_claim_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id"),
        nullable=True,
        index=True,
    )
    claim_calculation_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id"),
        nullable=True,
        index=True,
    )
    client_roadmap_document_id: Mapped[int | None] = mapped_column(
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
    pretrial_claim_document = relationship(
        "Document", foreign_keys=[pretrial_claim_document_id]
    )
    statement_of_claim_document = relationship(
        "Document", foreign_keys=[statement_of_claim_document_id]
    )
    claim_calculation_document = relationship(
        "Document", foreign_keys=[claim_calculation_document_id]
    )
    client_roadmap_document = relationship(
        "Document", foreign_keys=[client_roadmap_document_id]
    )


__all__ = ["SelfFilingPackage"]
