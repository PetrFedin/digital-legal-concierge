"""Client-only, non-delivery grant state for future secure archive downloads.

Separate from staff document_access_grants by design. This table contains
neither plaintext file bytes, encryption material nor bearer secrets.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ClientDocumentGrant(Base, TimestampMixin):
    __tablename__ = "client_document_grants"
    __table_args__ = (
        Index(
            "uq_client_document_grants_issued",
            "user_id", "document_id",
            unique=True,
            postgresql_where=text("status = 'ISSUED'"),
            sqlite_where=text("status = 'ISSUED'"),
        ),
        Index("ix_client_document_grants_user_expiry", "user_id", "expires_at"),
        Index("ix_client_document_grants_case_status", "case_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    document_version: Mapped[int] = mapped_column(Integer)
    document_sha256: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(32), default="PRIVATE_PORTAL")
    approval_reference: Mapped[str] = mapped_column(String(120))
    token_key_id: Mapped[str] = mapped_column(String(32))
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="ISSUED", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
