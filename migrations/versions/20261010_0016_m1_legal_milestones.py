"""Persist M1 legal milestone dates required by the frozen UX contract.

Revision ID: 20261010_0016
Revises: 20261010_0015
Create Date: 2026-10-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261010_0016"
down_revision = "20261010_0015"
branch_labels = None
depends_on = None


COLUMNS = (
    ("contract_signed_at", sa.DateTime(timezone=True)),
    ("poa_instruction_sent_at", sa.DateTime(timezone=True)),
    ("poa_received_at", sa.DateTime(timezone=True)),
    ("claim_sent_at", sa.DateTime(timezone=True)),
    ("claim_waiting_until", sa.DateTime(timezone=True)),
    ("developer_response_status", sa.String(length=100)),
    ("lawsuit_filed_at", sa.DateTime(timezone=True)),
    ("decision_date", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "cases" not in set(inspector.get_table_names()):
        return
    existing = {column["name"] for column in inspector.get_columns("cases")}
    missing = [(name, kind) for name, kind in COLUMNS if name not in existing]
    if missing:
        with op.batch_alter_table("cases") as batch:
            for name, kind in missing:
                batch.add_column(sa.Column(name, kind, nullable=True))
    inspector = inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes("cases")}
    if "ix_cases_claim_waiting_until" not in indexes:
        op.create_index(
            "ix_cases_claim_waiting_until",
            "cases",
            ["claim_waiting_until"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "cases" not in set(inspector.get_table_names()):
        return
    indexes = {item["name"] for item in inspector.get_indexes("cases")}
    if "ix_cases_claim_waiting_until" in indexes:
        op.drop_index("ix_cases_claim_waiting_until", table_name="cases")
    existing = {column["name"] for column in inspector.get_columns("cases")}
    removable = [name for name, _ in COLUMNS if name in existing]
    if removable:
        with op.batch_alter_table("cases") as batch:
            for name in reversed(removable):
                batch.drop_column(name)
