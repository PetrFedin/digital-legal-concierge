from sqlalchemy import String, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin

class AnalyticsEvent(Base, TimestampMixin):
    __tablename__ = 'analytics_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    event_name: Mapped[str] = mapped_column(String(100), index=True)
    user_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    case_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    route: Mapped[str | None] = mapped_column(String(10), nullable=True)
    source: Mapped[str] = mapped_column(String(100), default='telegram_bot')
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
