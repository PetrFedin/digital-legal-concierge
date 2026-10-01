from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


DERIVATIVE_SANITIZED_PDF = "SANITIZED_PDF"
DERIVATIVE_OCR_PDF = "OCR_PDF"

DERIVATIVE_PENDING = "PENDING"
DERIVATIVE_READY = "READY"
DERIVATIVE_FAILED = "FAILED"


class DocumentDerivative(Base, TimestampMixin):
    """Encrypted, reproducible output derived from an immutable source Document.

    Derivatives are intentionally not Document versions. They never enter the
    client upload/review lifecycle and can always be rebuilt from source
    evidence plus the recorded processor/provenance.
    """

    __tablename__ = "document_derivatives"
    __table_args__ = (
        UniqueConstraint(
            "source_document_id",
            "derivative_type",
            "source_sha256",
            "tool_name",
            "tool_version",
            name="uq_document_derivative_reproducible_identity",
        ),
        Index(
            "ux_document_derivatives_encryption_envelope_id",
            "encryption_envelope_id",
            unique=True,
        ),
        Index(
            "ix_document_derivatives_source_type",
            "source_document_id",
            "derivative_type",
        ),
        Index(
            "ix_document_derivatives_case_status",
            "case_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id"),
        index=True,
    )
    derivative_type: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), default=DERIVATIVE_PENDING, index=True)

    # Exact immutable source identity used to build this derivative.
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Canonical encrypted Case storage key, never a host absolute path.
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(100), default="application/pdf")
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)

    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_usable_text: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    encryption_status: Mapped[str] = mapped_column(String(32), default="PENDING")
    encryption_key_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    encryption_format_version: Mapped[int] = mapped_column(Integer, default=2)
    encryption_envelope_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    encrypted_data_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_data_key_nonce: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_key_destroyed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    encrypted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(255), nullable=True)

    source_document = relationship("Document")


__all__ = [
    "DERIVATIVE_FAILED",
    "DERIVATIVE_OCR_PDF",
    "DERIVATIVE_PENDING",
    "DERIVATIVE_READY",
    "DERIVATIVE_SANITIZED_PDF",
    "DocumentDerivative",
]
