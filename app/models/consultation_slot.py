from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class ConsultationSlot(Base, TimestampMixin):
    __tablename__ = "consultation_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    lawyer_id: Mapped[int] = mapped_column(ForeignKey("lawyers.id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(30), default="available", index=True)
    hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    held_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    # consultations.slot_id and consultation_slots.consultation_id form a
    # deliberate bidirectional integrity link. Mark this edge as the deferred
    # side for SQLAlchemy's DDL sorter so metadata and Alembic can order the
    # tables deterministically instead of silently ignoring both constraints.
    consultation_id: Mapped[int | None] = mapped_column(
        ForeignKey("consultations.id", use_alter=True),
        nullable=True,
        unique=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    lawyer = relationship("Lawyer")
    consultation = relationship("Consultation", foreign_keys=[consultation_id])
