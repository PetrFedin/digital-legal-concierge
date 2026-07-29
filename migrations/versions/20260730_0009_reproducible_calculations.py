"""Persist all inputs required to reproduce penalty calculations.

Revision ID: 20260730_0009
Revises: 20260729_0008
Create Date: 2026-07-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260730_0009"
down_revision = "20260729_0008"
branch_labels = None
depends_on = None


def _new_columns() -> dict[str, sa.Column]:
    # Return fresh Column instances for every migration invocation. Reusing
    # attached SQLAlchemy Column objects can make repeated migration smoke
    # tests non-deterministic in the same Python process.
    return {
        "calculation_date": sa.Column(
            "calculation_date", sa.Date(), nullable=True
        ),
        "key_rate": sa.Column(
            "key_rate", sa.Numeric(7, 6), nullable=True
        ),
        "consumer_multiplier": sa.Column(
            "consumer_multiplier", sa.Numeric(5, 2), nullable=True
        ),
        "formula_version": sa.Column(
            "formula_version", sa.String(length=50), nullable=True
        ),
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "calculations" not in inspector.get_table_names():
        return
    existing = {
        column["name"] for column in inspector.get_columns("calculations")
    }
    with op.batch_alter_table("calculations") as batch:
        for name, column in _new_columns().items():
            if name not in existing:
                batch.add_column(column)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "calculations" not in inspector.get_table_names():
        return
    existing = {
        column["name"] for column in inspector.get_columns("calculations")
    }
    with op.batch_alter_table("calculations") as batch:
        for name in reversed(tuple(_new_columns())):
            if name in existing:
                batch.drop_column(name)
