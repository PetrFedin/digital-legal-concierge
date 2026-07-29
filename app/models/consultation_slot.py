from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class ConsultationSlot(Base, TimestampMixin):
    __tablename__ = "consultation_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    lawyer_id: Mapped[int] = mapped_column(ForeignKey("lawyers.id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(30), default="available", index=True)
    hold_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    held_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )
    consultation_id: Mapped[int | None] = mapped_column(
        ForeignKey("consultations.id"),
        nullable=True,
        unique=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "ends_at > starts_at",
            name="ck_consultation_slots_positive_interval",
        ),
        Index(
            "uq_consultation_slots_active_lawyer_start",
            "lawyer_id",
            "starts_at",
            unique=True,
            sqlite_where=text("status IN ('held', 'booked')"),
            postgresql_where=text("status IN ('held', 'booked')"),
        ),
        ExcludeConstraint(
            ("lawyer_id", "="),
            (func.tstzrange(starts_at, ends_at, "[)"), "&&"),
            where=text("status IN ('held', 'booked')"),
            name="excl_consultation_slots_active_lawyer_overlap",
        ).ddl_if(dialect="postgresql"),
        ExcludeConstraint(
            ("held_by_user_id", "="),
            (func.tstzrange(starts_at, ends_at, "[)"), "&&"),
            where=text(
                "status IN ('held', 'booked') AND held_by_user_id IS NOT NULL"
            ),
            name="excl_consultation_slots_active_client_overlap",
        ).ddl_if(dialect="postgresql"),
    )

    lawyer = relationship("Lawyer")
    consultation = relationship("Consultation", foreign_keys=[consultation_id])
