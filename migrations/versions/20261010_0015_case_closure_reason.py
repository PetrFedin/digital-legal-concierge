"""Persist explicit case closure reason.

Revision ID: 20261010_0015
Revises: 20261006_0014
Create Date: 2026-10-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261010_0015"
down_revision = "20261006_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "cases" not in set(inspector.get_table_names()):
        return
    existing = {column["name"] for column in inspector.get_columns("cases")}
    if "closure_reason" in existing:
        return
    with op.batch_alter_table("cases") as batch:
        batch.add_column(sa.Column("closure_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "cases" not in set(inspector.get_table_names()):
        return
    existing = {column["name"] for column in inspector.get_columns("cases")}
    if "closure_reason" not in existing:
        return
    with op.batch_alter_table("cases") as batch:
        batch.drop_column("closure_reason")
