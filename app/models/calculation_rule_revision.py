from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CalculationRuleRevision(Base, TimestampMixin):
    """Versioned, effective-dated legal calculator rule bundle.

    The row stores the complete rule payload plus a canonical SHA-256. A legal
    review is bound to that exact hash before a separate production approval can
    make the revision resolvable. APPROVED/RETIRED rows are never edited;
    calculations copy the rule snapshot into immutable history.
    """

    __tablename__ = "calculation_rule_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    revision_key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="DRAFT",
        index=True,
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    rules: Mapped[dict] = mapped_column(JSON, nullable=False)
    rules_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    legal_reviewed_by_actor_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    legal_reviewed_by_actor_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    legal_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    legal_review_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    legal_review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    approved_by_actor_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    approved_by_actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
