from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DocumentAccessGrant(Base, TimestampMixin):
    __tablename__ = "document_access_grants"
    __table_args__ = (
        Index("ix_document_access_grants_actor_active", "actor_account_id", "expires_at"),
        Index("ix_document_access_grants_document_created", "document_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    actor_account_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), index=True)
    actor_role: Mapped[str] = mapped_column(String(32))
    session_jti_ref: Mapped[str] = mapped_column(String(80), index=True)
    session_version: Mapped[int] = mapped_column(Integer)
    token_key_id: Mapped[str] = mapped_column(String(32))
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    purpose: Mapped[str] = mapped_column(String(64), default="download")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    client_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
