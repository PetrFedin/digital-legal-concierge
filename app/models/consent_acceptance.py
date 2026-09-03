from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ConsentAcceptance(Base, TimestampMixin):
    """Immutable evidence of one explicit personal-data consent decision."""

    __tablename__ = "consent_acceptances"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source_callback_id",
            name="uq_consent_user_callback",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    consent_type: Mapped[str] = mapped_column(String(50), nullable=False)
    consent_status: Mapped[str] = mapped_column(String(20), nullable=False)
    consent_version: Mapped[str] = mapped_column(String(100), nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    text_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    consent_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_callback_id: Mapped[str] = mapped_column(String(255), nullable=False)
