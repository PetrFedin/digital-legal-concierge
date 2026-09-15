from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class CalculationSegment(Base):
    """Immutable interval evidence for one completed preliminary calculation."""

    __tablename__ = "calculation_segments"
    __table_args__ = (
        UniqueConstraint(
            "calculation_id",
            "sequence_no",
            name="uq_calculation_segment_sequence",
        ),
        CheckConstraint("sequence_no > 0", name="ck_calculation_segment_sequence_positive"),
        CheckConstraint("period_to >= period_from", name="ck_calculation_segment_range"),
        CheckConstraint("days_total >= 0", name="ck_calculation_segment_days_total"),
        CheckConstraint("days_excluded >= 0", name="ck_calculation_segment_days_excluded"),
        CheckConstraint("days_chargeable >= 0", name="ck_calculation_segment_days_chargeable"),
        CheckConstraint(
            "days_chargeable + days_excluded = days_total",
            name="ck_calculation_segment_day_balance",
        ),
        CheckConstraint("amount >= 0", name="ck_calculation_segment_amount_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    calculation_id: Mapped[int] = mapped_column(
        ForeignKey("calculations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    rule_set_id: Mapped[int] = mapped_column(
        ForeignKey("calculation_rule_sets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    rule_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    period_from: Mapped[date] = mapped_column(Date, nullable=False)
    period_to: Mapped[date] = mapped_column(Date, nullable=False)
    days_total: Mapped[int] = mapped_column(Integer, nullable=False)
    days_excluded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    days_chargeable: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_period_id: Mapped[int | None] = mapped_column(
        ForeignKey("calculation_rate_periods.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    rate_value: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    consumer_multiplier: Mapped[Decimal] = mapped_column(
        Numeric(12, 8), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    exclusion_evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    calculation = relationship("Calculation", back_populates="segments")
    rule_set = relationship("CalculationRuleSet")
    rate_period = relationship("CalculationRatePeriod")
