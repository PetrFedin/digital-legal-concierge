"""Persist bank payment purpose and requisites snapshot.

Revision ID: 20260928_0029
Revises: 20260928_0028
Create Date: 2026-09-28

Self-filing payments are made only by bank transfer to the customer-approved
bar association account. The exact purpose and bank requisites shown to the
client are snapshotted on the Payment so reopening an old obligation never
silently picks up different future details.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260928_0029"
down_revision = "20260928_0028"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    inspector = inspect(bind)
    if table not in set(inspector.get_table_names()):
        return set()
    return {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "payments" not in set(inspect(bind).get_table_names()):
        return
    cols = _columns(bind, "payments")
    with op.batch_alter_table("payments") as batch:
        if "payment_purpose" not in cols:
            batch.add_column(sa.Column("payment_purpose", sa.String(length=500), nullable=True))
        if "payment_details_snapshot" not in cols:
            batch.add_column(sa.Column("payment_details_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "payments" not in set(inspect(bind).get_table_names()):
        return
    cols = _columns(bind, "payments")
    with op.batch_alter_table("payments") as batch:
        if "payment_details_snapshot" in cols:
            batch.drop_column("payment_details_snapshot")
        if "payment_purpose" in cols:
            batch.drop_column("payment_purpose")
