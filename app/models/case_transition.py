from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CaseTransitionCommand(Base, TimestampMixin):
    """Durable idempotency/recovery journal for Case process transitions.

    One external command key may affect one Case at most once. The row is written
    in the same database transaction as the Case mutation, AuditLog history and
    outbox event, so a retry after commit-before-response can recover the already
    applied result without creating a second legal/process transition.
    """

    __tablename__ = "case_transition_commands"
    __table_args__ = (
        UniqueConstraint(
            "case_id",
            "idempotency_key",
            name="uq_case_transition_commands_case_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    request_client_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    actor_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    expected_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    applied_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_status: Mapped[str] = mapped_column(String(100), nullable=False)
    target_status: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="APPLIED",
        index=True,
    )
    result_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class CaseTransitionOutboxEvent(Base, TimestampMixin):
    """Transactional outbox evidence for an applied Case transition."""

    __tablename__ = "case_transition_outbox_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        unique=True,
        index=True,
    )
    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    command_id: Mapped[int] = mapped_column(
        ForeignKey("case_transition_commands.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="PENDING",
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


__all__ = ["CaseTransitionCommand", "CaseTransitionOutboxEvent"]
