from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Consultation(Base, TimestampMixin):
    __tablename__ = "consultations"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    lawyer_id: Mapped[int | None] = mapped_column(ForeignKey("lawyers.id"), nullable=True)
    slot_id: Mapped[int | None] = mapped_column(ForeignKey("consultation_slots.id"), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(100), default="DESCRIPTION_PENDING", index=True)
    consultation_type: Mapped[str] = mapped_column(String(100), default="online")
    subject_type: Mapped[str] = mapped_column(String(50), default="existing_case")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    client_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    lawyer_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(100), nullable=True)

    case = relationship("Case", back_populates="consultations")
    lawyer = relationship("Lawyer", back_populates="consultations")
    slot = relationship("ConsultationSlot", foreign_keys=[slot_id])
