from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)
    assigned_lawyer_id: Mapped[int | None] = mapped_column(ForeignKey("lawyers.id"), nullable=True, index=True)
    created_by_admin_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="OPEN", index=True)
    priority: Mapped[str] = mapped_column(String(20), default="NORMAL", index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    case = relationship("Case")
    assigned_lawyer = relationship("Lawyer")
    created_by = relationship("AdminUser")
