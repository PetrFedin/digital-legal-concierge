from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int | None] = mapped_column(
        ForeignKey("cases.id"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )
    channel: Mapped[str] = mapped_column(String(50), default="telegram")
    event_code: Mapped[str] = mapped_column(String(100), index=True)
    recipient_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        index=True,
    )
    target_chat_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
    )
    dedupe_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(100),
        default="PENDING",
        index=True,
    )
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    case = relationship("Case", back_populates="notifications")
