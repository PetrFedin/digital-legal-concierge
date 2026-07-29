from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Document(Base, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_case_sha256", "case_id", "sha256", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )
    document_type: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(255))
    file_name: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(500))
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    detected_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    security_status: Mapped[str] = mapped_column(
        String(32),
        default="LEGACY_UNVERIFIED",
        index=True,
    )
    security_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scanned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(100), default="UPLOADED")
    is_required: Mapped[bool] = mapped_column(Boolean, default=False)
    lawyer_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    case = relationship("Case", back_populates="documents")
