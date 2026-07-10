from sqlalchemy import String, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin

class SystemSetting(Base, TimestampMixin):
    __tablename__ = 'system_settings'
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    value: Mapped[dict] = mapped_column(JSON)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_editable_in_admin: Mapped[bool] = mapped_column(Boolean, default=True)
