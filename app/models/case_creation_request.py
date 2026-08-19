from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CaseCreationRequest(Base, TimestampMixin):
    """Idempotency ledger for one explicit client action that creates a Case.

    The product permits several active Cases per client. Duplicate Telegram
    delivery therefore cannot be prevented by a client-wide uniqueness rule;
    it must be deduplicated by the concrete source operation instead.
    """

    __tablename__ = "case_creation_requests"
    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "operation_key",
            name="uq_case_creation_client_operation",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    operation_key: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(String(50), nullable=False)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
