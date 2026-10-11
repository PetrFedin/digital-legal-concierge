"""Persist B-007 consent decisions without inventing a new case route.

Revision ID: 20261011_0018
Revises: 20261010_0017
"""
from alembic import op
import sqlalchemy as sa

revision = "20261011_0018"
down_revision = "20261010_0017"
branch_labels = None
depends_on = None

def upgrade() -> None:
    with op.batch_alter_table("cases") as batch:
        batch.add_column(sa.Column("consent_status", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("consent_date", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("decline_date", sa.DateTime(timezone=True), nullable=True))

def downgrade() -> None:
    with op.batch_alter_table("cases") as batch:
        batch.drop_column("decline_date")
        batch.drop_column("consent_date")
        batch.drop_column("consent_status")
