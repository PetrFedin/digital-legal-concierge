from sqlalchemy import ForeignKey, String, Integer, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin

class Document(Base, TimestampMixin):
    __tablename__ = 'documents'
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey('cases.id'), index=True)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    document_type: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(255))
    file_name: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(500))
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(100), default='UPLOADED')
    is_required: Mapped[bool] = mapped_column(Boolean, default=False)
    lawyer_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    case = relationship('Case', back_populates='documents')
