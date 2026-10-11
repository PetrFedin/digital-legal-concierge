"""Persist the B-007 consent decision on upgraded legacy case records.

Revision ID: 20261011_0018
Revises: 20261010_0017

Revision 0001 creates the full current ORM schema on a fresh database,
while upgrade installations may have only the old Case columns.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261011_0018"
down_revision = "20261010_0017"
branch_labels = None
depends_on = None

_COLUMNS = {
    "consent_status": sa.String(length=20),
    "consent_date": sa.DateTime(timezone=True),
    "decline_date": sa.DateTime(timezone=True),
}

def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "cases" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("cases")}
    missing = [
        sa.Column(name, type_, nullable=True)
        for name, type_ in _COLUMNS.items() if name not in existing
    ]
    if missing:
        with op.batch_alter_table("cases") as batch:
            for column in missing:
                batch.add_column(column)

def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "cases" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("cases")}
    present = [name for name in reversed(tuple(_COLUMNS)) if name in existing]
    if present:
        with op.batch_alter_table("cases") as batch:
            for name in present:
                batch.drop_column(name)
