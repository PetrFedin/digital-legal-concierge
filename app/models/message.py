from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Message(Base, TimestampMixin):
    __tablename__ = "messages"
    __table_args__ = (
        Index(
            "uq_messages_sender_source_message",
            "sender_type",
            "sender_id",
            "source_message_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    sender_type: Mapped[str] = mapped_column(String(50))
    sender_id: Mapped[int | None] = mapped_column(nullable=True)
    source_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    case = relationship("Case", back_populates="messages")
