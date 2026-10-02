from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class CaseRetentionRecord(Base, TimestampMixin):
    __tablename__ = "case_retention_records"
    __table_args__ = (
        UniqueConstraint("case_id", name="uq_case_retention_case"),
        Index("ix_case_retention_status_due", "status", "retention_due_at"),
        Index("ix_case_retention_hold_due", "legal_hold", "retention_due_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    policy_version: Mapped[str] = mapped_column(
        String(50), default="case-content-v1"
    )
    status: Mapped[str] = mapped_column(
        String(32), default="DISCOVERED", index=True
    )
    retention_due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )

    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    legal_hold_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_hold_set_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    legal_hold_set_by: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id"), nullable=True
    )
    legal_hold_released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    legal_hold_released_by: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id"), nullable=True
    )

    requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    requested_by: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id"), nullable=True
    )
    request_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id"), nullable=True
    )
    approval_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    execution_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)

    documents_deleted: Mapped[int] = mapped_column(Integer, default=0)
    derivatives_deleted: Mapped[int] = mapped_column(Integer, default=0)
    messages_deleted: Mapped[int] = mapped_column(Integer, default=0)
    notifications_deleted: Mapped[int] = mapped_column(Integer, default=0)
    consultations_anonymized: Mapped[int] = mapped_column(Integer, default=0)
    content_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)

    case = relationship("Case", back_populates="retention_record")
