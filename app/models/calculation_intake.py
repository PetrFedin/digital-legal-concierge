from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class CalculationIntake(Base, TimestampMixin):
    """Durable Case-bound calculator questionnaire state.

    Redis/FSM may mirror these values for conversational UX, but this row owns
    accepted calculator facts until the questionnaire is completed or explicitly
    reset. One Case has one current intake; completed Calculation rows preserve
    immutable historical results.
    """

    __tablename__ = "calculation_intakes"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    contract_price: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    planned_transfer_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    object_transferred: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    actual_transfer_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    calculation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    current_step: Mapped[str] = mapped_column(
        String(50), nullable=False, default="price", index=True
    )
    source: Mapped[str] = mapped_column(
        String(50), nullable=False, default="telegram_bot"
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    case = relationship("Case", back_populates="calculation_intake")
