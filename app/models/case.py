from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Case(Base, TimestampMixin):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_number: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    route: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(100), default="telegram_bot")
    assigned_lawyer_id: Mapped[int | None] = mapped_column(ForeignKey("lawyers.id"), nullable=True, index=True)
    next_action: Mapped[str | None] = mapped_column(String(255), nullable=True)
    internal_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    client = relationship("User", back_populates="cases")
    lawyer = relationship("Lawyer", back_populates="cases")
    calculation = relationship("Calculation", back_populates="case", uselist=False)
    documents = relationship("Document", back_populates="case")
    payments = relationship("Payment", back_populates="case")
    consultations = relationship("Consultation", back_populates="case", foreign_keys="Consultation.case_id")
    messages = relationship("Message", back_populates="case")
    notifications = relationship("Notification", back_populates="case")
