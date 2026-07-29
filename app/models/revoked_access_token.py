from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class RevokedAccessToken(Base, TimestampMixin):
    __tablename__ = "revoked_access_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id"), nullable=True, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(String(100), default="logout", nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
