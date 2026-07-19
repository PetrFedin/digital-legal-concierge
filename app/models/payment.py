from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.payments.payment_processing_outcomes import PaymentProcessingOutcome
from app.models.base import Base, TimestampMixin


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"
    __table_args__ = (
        # Keep this deployed migration name. Renaming it requires an explicit
        # drop/create migration for existing SQLite and PostgreSQL databases.
        Index(
            "uq_payments_provider_payment_id",
            "provider",
            "provider_payment_id",
            unique=True,
            sqlite_where=text("provider IS NOT NULL AND provider_payment_id IS NOT NULL"),
            postgresql_where=text("provider IS NOT NULL AND provider_payment_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    payment_code: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(10), default="RUB")
    status: Mapped[str] = mapped_column(String(100), default="PENDING", index=True)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payment_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    processing_outcome: Mapped[PaymentProcessingOutcome | None] = mapped_column(
        Enum(
            PaymentProcessingOutcome,
            name="payment_processing_outcome",
            native_enum=False,
            length=50,
            values_callable=lambda outcome_enum: [outcome.value for outcome in outcome_enum],
        ),
        nullable=True,
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        info={
            "semantics": (
                "Timestamp when successful webhook processing completed; "
                "not the payment timestamp."
            )
        },
    )
    # Contract only; PaymentService will enforce it without ORM validators or DB checks:
    # true exactly for MANUAL_REVIEW_REQUIRED and CONFLICT, false for PROCESSED.
    manual_review_required: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
        info={
            "contract": (
                "True exactly for MANUAL_REVIEW_REQUIRED and CONFLICT outcomes; "
                "False for PROCESSED."
            )
        },
    )
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    case = relationship("Case", back_populates="payments")
