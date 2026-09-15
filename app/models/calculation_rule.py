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
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

RULE_STATUS_DRAFT = "DRAFT"
RULE_STATUS_APPROVED = "APPROVED"
RULE_STATUS_RETIRED = "RETIRED"
RULE_STATUSES = frozenset(
    {RULE_STATUS_DRAFT, RULE_STATUS_APPROVED, RULE_STATUS_RETIRED}
)


class CalculationDateRule(Base, TimestampMixin):
    """Versioned date-handling directory entry used by approved calculations."""

    __tablename__ = "calculation_date_rules"
    __table_args__ = (
        UniqueConstraint("rule_code", "revision", name="uq_calc_date_rule_revision"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_calc_date_rule_effective_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RULE_STATUS_DRAFT, index=True
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    strategy_code: Mapped[str] = mapped_column(String(100), nullable=False)
    parameters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by_actor_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    approved_by_actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    rule_sets = relationship("CalculationRuleSet", back_populates="date_rule")


class CalculationClientTypeRule(Base, TimestampMixin):
    """Versioned client-type coefficient directory entry.

    The MVP specification names physical person / other type only when
    applicable. This entity stores an approved coefficient without inventing a
    new Telegram question; classification remains a separate legal/business
    decision.
    """

    __tablename__ = "calculation_client_type_rules"
    __table_args__ = (
        UniqueConstraint(
            "client_type_code",
            "revision",
            name="uq_calc_client_type_rule_revision",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_calc_client_type_rule_effective_range",
        ),
        CheckConstraint(
            "consumer_multiplier > 0",
            name="ck_calc_client_type_multiplier_positive",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_type_code: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RULE_STATUS_DRAFT, index=True
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    consumer_multiplier: Mapped[Decimal] = mapped_column(
        Numeric(12, 8), nullable=False
    )
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by_actor_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    approved_by_actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    rule_sets = relationship("CalculationRuleSet", back_populates="client_type_rule")


class CalculationRuleSet(Base, TimestampMixin):
    """Immutable-after-approval legal calculation rule revision."""

    __tablename__ = "calculation_rule_sets"
    __table_args__ = (
        UniqueConstraint("code", "revision", name="uq_calc_rule_set_revision"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_calc_rule_set_effective_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RULE_STATUS_DRAFT, index=True
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    formula_code: Mapped[str] = mapped_column(String(100), nullable=False)
    formula_parameters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    rounding_code: Mapped[str] = mapped_column(String(50), nullable=False)
    date_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("calculation_date_rules.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    client_type_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("calculation_client_type_rules.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by_actor_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    approved_by_actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    date_rule = relationship("CalculationDateRule", back_populates="rule_sets")
    client_type_rule = relationship(
        "CalculationClientTypeRule", back_populates="rule_sets"
    )
    rate_periods = relationship(
        "CalculationRatePeriod",
        back_populates="rule_set",
        order_by="CalculationRatePeriod.valid_from",
    )
    exclusion_periods = relationship(
        "CalculationExclusionPeriod",
        back_populates="rule_set",
        order_by="CalculationExclusionPeriod.date_from",
    )
    calculations = relationship("Calculation", back_populates="rule_set")


class CalculationRatePeriod(Base, TimestampMixin):
    """Effective rate interval belonging to one legal rule revision."""

    __tablename__ = "calculation_rate_periods"
    __table_args__ = (
        UniqueConstraint(
            "rule_set_id",
            "valid_from",
            "valid_to",
            "rate_code",
            name="uq_calc_rate_period_identity",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_calc_rate_period_range",
        ),
        CheckConstraint("rate_value >= 0", name="ck_calc_rate_value_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_set_id: Mapped[int] = mapped_column(
        ForeignKey("calculation_rule_sets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    rate_code: Mapped[str] = mapped_column(String(100), nullable=False)
    rate_value: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)

    rule_set = relationship("CalculationRuleSet", back_populates="rate_periods")


class CalculationExclusionPeriod(Base, TimestampMixin):
    """Moratorium or other non-chargeable interval for one rule revision."""

    __tablename__ = "calculation_exclusion_periods"
    __table_args__ = (
        UniqueConstraint(
            "rule_set_id",
            "date_from",
            "date_to",
            "exclusion_type",
            name="uq_calc_exclusion_period_identity",
        ),
        CheckConstraint(
            "date_to >= date_from",
            name="ck_calc_exclusion_period_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_set_id: Mapped[int] = mapped_column(
        ForeignKey("calculation_rule_sets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    date_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    date_to: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    exclusion_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)

    rule_set = relationship("CalculationRuleSet", back_populates="exclusion_periods")
