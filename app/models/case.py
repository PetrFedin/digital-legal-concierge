from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Case(Base, TimestampMixin):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_number: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        index=True,
    )
    client_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        index=True,
    )
    route: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(
        String(100),
        default="telegram_bot",
    )
    assigned_lawyer_id: Mapped[int | None] = mapped_column(
        ForeignKey("lawyers.id"),
        nullable=True,
        index=True,
    )
    assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    first_lawyer_response_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_lawyer_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    sla_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    sla_status: Mapped[str] = mapped_column(
        String(50),
        default="NOT_STARTED",
        index=True,
    )
    escalation_level: Mapped[int] = mapped_column(Integer, default=0)
    next_action: Mapped[str | None] = mapped_column(String(255), nullable=True)
    internal_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    closure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # B-007: consent evidence is a Case-owned fact, not an inferred status.
    consent_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    consent_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decline_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    contract_signed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    poa_instruction_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    poa_received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claim_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claim_waiting_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    developer_response_status: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    lawsuit_filed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decision_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enforcement_number: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    enforcement_status: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    enforcement_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    received_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    success_fee_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    content_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    client = relationship("User", back_populates="cases")
    lawyer = relationship("Lawyer", back_populates="cases")
    calculation = relationship(
        "Calculation",
        back_populates="case",
        uselist=False,
    )
    documents = relationship("Document", back_populates="case")
    payments = relationship("Payment", back_populates="case")
    consultations = relationship(
        "Consultation",
        back_populates="case",
        foreign_keys="Consultation.case_id",
    )
    court_events = relationship(
        "CourtEvent",
        back_populates="case",
        order_by="CourtEvent.event_date",
    )
    messages = relationship("Message", back_populates="case")
    notifications = relationship("Notification", back_populates="case")
    retention_record = relationship(
        "CaseRetentionRecord", back_populates="case", uselist=False
    )
