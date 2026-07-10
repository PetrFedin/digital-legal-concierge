from sqlalchemy import String, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin

class Lawyer(Base, TimestampMixin):
    __tablename__ = 'lawyers'
    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    specialization: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    workload_limit: Mapped[int] = mapped_column(Integer, default=30)
    cases = relationship('Case', back_populates='lawyer')
    consultations = relationship('Consultation', back_populates='lawyer')
