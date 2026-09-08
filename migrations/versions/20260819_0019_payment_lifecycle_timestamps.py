"""Persist immutable payment lifecycle timestamps.

Revision ID: 20260819_0019
Revises: 20260819_0018
Create Date: 2026-08-19

updated_at is intentionally not used as paid/refunded time because review,
reconciliation and refund workflows mutate a Payment after the money event.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260819_0019"
down_revision = "20260819_0018"
branch_labels = None
depends_on = None

_COLUMNS = {
    "paid_at": sa.DateTime(timezone=True),
    "failed_at": sa.DateTime(timezone=True),
    "cancelled_at": sa.DateTime(timezone=True),
    "refunded_at": sa.DateTime(timezone=True),
    "expired_at": sa.DateTime(timezone=True),
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "payments" not in set(inspector.get_table_names()):
        return

    existing = {column["name"] for column in inspector.get_columns("payments")}
    with op.batch_alter_table("payments") as batch:
        for name, column_type in _COLUMNS.items():
            if name not in existing:
                batch.add_column(sa.Column(name, column_type, nullable=True))

    inspector = inspect(bind)
    indexes = {
        item.get("name")
        for item in inspector.get_indexes("payments")
        if item.get("name")
    }
    if "ix_payments_paid_at" not in indexes:
        op.create_index("ix_payments_paid_at", "payments", ["paid_at"], unique=False)
    if "ix_payments_refunded_at" not in indexes:
        op.create_index(
            "ix_payments_refunded_at",
            "payments",
            ["refunded_at"],
            unique=False,
        )

    # Conservative historical backfill: only terminal/current statuses that
    # unambiguously prove the money event happened. created_at/updated_at cannot
    # reconstruct the exact provider timestamp, so we use the best available
    # legacy audit-adjacent value and never pretend it is exact provider time.
    op.execute(
        sa.text(
            """
            UPDATE payments
            SET paid_at = COALESCE(paid_at, updated_at, created_at)
            WHERE paid_at IS NULL
              AND status IN ('PAID', 'PAID_REVIEW', 'REFUND_PENDING',
                             'REFUND_DECLINED', 'REFUNDED')
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE payments
            SET failed_at = COALESCE(failed_at, updated_at, created_at)
            WHERE failed_at IS NULL AND status = 'FAILED'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE payments
            SET cancelled_at = COALESCE(cancelled_at, updated_at, created_at)
            WHERE cancelled_at IS NULL AND status = 'CANCELLED'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE payments
            SET refunded_at = COALESCE(refunded_at, updated_at, created_at)
            WHERE refunded_at IS NULL AND status = 'REFUNDED'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE payments
            SET expired_at = COALESCE(expired_at, updated_at, created_at)
            WHERE expired_at IS NULL AND status = 'EXPIRED'
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "payments" not in set(inspector.get_table_names()):
        return

    indexes = {
        item.get("name")
        for item in inspector.get_indexes("payments")
        if item.get("name")
    }
    if "ix_payments_refunded_at" in indexes:
        op.drop_index("ix_payments_refunded_at", table_name="payments")
    if "ix_payments_paid_at" in indexes:
        op.drop_index("ix_payments_paid_at", table_name="payments")

    existing = {column["name"] for column in inspect(bind).get_columns("payments")}
    with op.batch_alter_table("payments") as batch:
        for name in reversed(tuple(_COLUMNS)):
            if name in existing:
                batch.drop_column(name)
