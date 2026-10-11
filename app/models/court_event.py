from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class CourtEvent(Base, TimestampMixin):
    __tablename__ = "court_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    lawyer_id: Mapped[int | None] = mapped_column(
        ForeignKey("lawyers.id"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    event_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )
    court_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    court_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachments_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    case = relationship("Case", back_populates="court_events")
    lawyer = relationship("Lawyer")
