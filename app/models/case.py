from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, event
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
    # M1 is one legal route with more than one commercial service mode.
    # SELF_FILING_PACKAGE must never be represented as a third route.
    service_mode: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(100), index=True)
    # Monotonic aggregate version used by CaseService for optimistic/stale-action
    # protection. It advances exactly once for each persisted process transition.
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
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
    # Client activity is intentionally separate from updated_at: staff work,
    # scheduler changes and reconciliation must not postpone client re-engagement.
    last_client_action_at: Mapped[datetime | None] = mapped_column(
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
    # Business closure is distinct from archive/read-only and retention deletion.
    close_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    content_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    client = relationship("User", back_populates="cases")
    lawyer = relationship("Lawyer", back_populates="cases")
    calculations = relationship(
        "Calculation",
        back_populates="case",
        order_by="Calculation.created_at",
    )
    calculation_intake = relationship(
        "CalculationIntake",
        back_populates="case",
        uselist=False,
        cascade="all, delete-orphan",
    )
    documents = relationship("Document", back_populates="case")
    payments = relationship("Payment", back_populates="case")
    consultations = relationship(
        "Consultation",
        back_populates="case",
        foreign_keys="Consultation.case_id",
    )
    messages = relationship("Message", back_populates="case")
    notifications = relationship("Notification", back_populates="case")
    retention_record = relationship(
        "CaseRetentionRecord", back_populates="case", uselist=False
    )
    self_filing_package = relationship(
        "SelfFilingPackage",
        back_populates="case",
        uselist=False,
        cascade="all, delete-orphan",
    )


_BUSINESS_CLOSED = {
    "M1_CLOSED": "M1_COMPLETED",
    "M2_CLOSED": "M2_COMPLETED",
}
_TERMINAL = frozenset({*_BUSINESS_CLOSED, "ARCHIVED"})
# Legacy database rows may still contain this historical M2 bootstrap value and
# must remain readable so ConsultationIntakeService can advance them. New ORM
# writes must never recreate/re-enter it; the canonical M2 start state is
# M2_DESCRIPTION_PENDING.
_WRITE_FORBIDDEN_COMPATIBILITY_STATUSES = frozenset({"M2_CONSULTATION_ROUTE"})


def _status_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().upper()


@event.listens_for(Case.status, "set", active_history=True)
def _stamp_case_lifecycle_fact(
    target: Case,
    value: object,
    oldvalue: object,
    _initiator,
) -> None:
    """Protect lifecycle facts and historical compatibility states on writes.

    CaseService remains the process-state owner. This listener is a defensive
    persistence backstop: it protects lifecycle facts from omission and rejects
    recreation of retired compatibility-only statuses. SQLAlchemy row hydration
    does not use this application-level assignment path, so historical rows stay
    readable and can move forward through the explicit compatibility transition.
    """

    new_status = _status_value(value)
    old_status = _status_value(oldvalue)
    if not new_status or new_status == old_status:
        return

    if new_status in _WRITE_FORBIDDEN_COMPATIBILITY_STATUSES:
        raise ValueError(
            f"Статус {new_status} доступен только для чтения исторических данных; "
            "новые записи и повторный вход в него запрещены"
        )

    now = datetime.now(timezone.utc)
    if new_status in _BUSINESS_CLOSED:
        if target.closed_at is None:
            target.closed_at = now
        if not str(target.close_reason or "").strip():
            target.close_reason = _BUSINESS_CLOSED[new_status]
    elif new_status == "ARCHIVED":
        if target.closed_at is None:
            target.closed_at = now
        if target.archived_at is None:
            target.archived_at = now
        if not str(target.close_reason or "").strip():
            target.close_reason = "ARCHIVED_LEGACY"

    # Reopening a terminal Case is only permitted through CaseService's forced
    # recovery policy. When it happens, terminal lifecycle facts must not remain
    # attached to a live matter.
    if old_status in _TERMINAL and new_status not in _TERMINAL:
        target.closed_at = None
        target.archived_at = None
        target.close_reason = None