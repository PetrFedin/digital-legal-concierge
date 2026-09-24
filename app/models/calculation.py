from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Integer, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Calculation(Base, TimestampMixin):
    __tablename__ = "calculations"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id"),
        index=True,
    )
    contract_price: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    planned_transfer_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    calculation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_transfer_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    object_transferred: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    deadline_confirmed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    unique_object: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    acceptance_evasion: Mapped[str | None] = mapped_column(String(30), nullable=True)
    ddu_signing_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Legacy delay_days remains readable for existing rows and route guards.
    # New rule-based calculations also persist the explicit specification facts.
    delay_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delay_days_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delay_days_chargeable: Mapped[int | None] = mapped_column(Integer, nullable=True)
    moratorium_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Legacy single-rate fields remain for historical compatibility. For a
    # multi-rate calculation key_rate is NULL and applied_segments is canonical.
    key_rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 6), nullable=True)
    consumer_multiplier: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 4), nullable=True
    )
    client_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    penalty_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    formula_version: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )

    rule_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("calculation_rule_revisions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    rule_revision_key: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    rule_snapshot_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    rule_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    applied_segments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    base_rate_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    calculation_branch: Mapped[str | None] = mapped_column(String(30), nullable=True)
    penalty_cap_applied: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    applied_source_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)

    is_preliminary: Mapped[bool] = mapped_column(Boolean, default=True)

    case = relationship("Case", back_populates="calculations")
    rule_revision = relationship("CalculationRuleRevision")
