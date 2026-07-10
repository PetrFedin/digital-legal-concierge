from datetime import date
from decimal import Decimal
from sqlalchemy import ForeignKey, Numeric, Date, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin

class Calculation(Base, TimestampMixin):
    __tablename__ = 'calculations'
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey('cases.id'), unique=True)
    contract_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    planned_transfer_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_transfer_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    object_transferred: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    delay_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    penalty_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    is_preliminary: Mapped[bool] = mapped_column(Boolean, default=True)
    case = relationship('Case', back_populates='calculation')
