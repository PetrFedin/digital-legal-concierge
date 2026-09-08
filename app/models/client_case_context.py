from __future__ import annotations

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ClientCaseContext(Base, TimestampMixin):
    """Persist which Case the Telegram cabinet currently operates on.

    A client may legitimately have several active matters. The selected Case is
    therefore navigation state, not a global uniqueness constraint on legal
    matters. Mutating flows should still bind actions to an explicit case_id;
    this context is the safe default for persistent-menu navigation.
    """

    __tablename__ = "client_case_contexts"

    client_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    selected_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("cases.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
