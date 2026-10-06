"""Persist M1 enforcement execution and actual receipts.

Revision ID: 20261006_0014
Revises: 20260806_0013
Create Date: 2026-10-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261006_0014"
down_revision = "20260806_0013"
branch_labels = None
depends_on = None


COLUMNS = (
    ("enforcement_number", sa.String(length=255)),
    ("enforcement_status", sa.String(length=100)),
    ("enforcement_started_at", sa.DateTime(timezone=True)),
    ("received_amount", sa.Numeric(14, 2)),
    ("received_at", sa.DateTime(timezone=True)),
    ("success_fee_amount", sa.Numeric(14, 2)),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "cases" not in set(inspector.get_table_names()):
        return

    existing = {column["name"] for column in inspector.get_columns("cases")}
    missing = [(name, column_type) for name, column_type in COLUMNS if name not in existing]
    if not missing:
        return

    with op.batch_alter_table("cases") as batch:
        for name, column_type in missing:
            batch.add_column(sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "cases" not in set(inspector.get_table_names()):
        return

    existing = {column["name"] for column in inspector.get_columns("cases")}
    removable = [name for name, _ in COLUMNS if name in existing]
    if not removable:
        return

    with op.batch_alter_table("cases") as batch:
        for name in reversed(removable):
            batch.drop_column(name)
