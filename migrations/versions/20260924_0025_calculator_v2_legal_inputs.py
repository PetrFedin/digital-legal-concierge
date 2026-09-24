"""Add PM-016 v2 calculator inputs and result evidence.

Revision ID: 20260924_0025
Revises: 20260918_0024
Create Date: 2026-09-24

The migration adds only schema needed to persist factual questionnaire answers
and the exact v2 calculation projection. It deliberately does not seed or
approve legal rates/rules. Legal authority remains an explicit audited DRAFT →
APPROVED operation.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260924_0025"
down_revision = "20260918_0024"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    inspector = inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    intake_columns = _columns(bind, "calculation_intakes")
    if intake_columns:
        additions = (
            ("client_type", sa.String(length=50)),
            ("deadline_confirmed", sa.Boolean()),
            ("unique_object", sa.Boolean()),
            ("acceptance_evasion", sa.String(length=30)),
            ("ddu_signing_date", sa.Date()),
        )
        with op.batch_alter_table("calculation_intakes") as batch:
            for name, type_ in additions:
                if name not in intake_columns:
                    batch.add_column(sa.Column(name, type_, nullable=True))

    calculation_columns = _columns(bind, "calculations")
    if calculation_columns:
        additions = (
            ("deadline_confirmed", sa.Boolean()),
            ("unique_object", sa.Boolean()),
            ("acceptance_evasion", sa.String(length=30)),
            ("ddu_signing_date", sa.Date()),
            ("base_rate_date", sa.Date()),
            ("calculation_branch", sa.String(length=30)),
            ("penalty_cap_applied", sa.Boolean()),
            ("applied_source_ids", sa.JSON()),
        )
        with op.batch_alter_table("calculations") as batch:
            for name, type_ in additions:
                if name not in calculation_columns:
                    batch.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()

    calculation_columns = _columns(bind, "calculations")
    if calculation_columns:
        with op.batch_alter_table("calculations") as batch:
            for name in (
                "applied_source_ids",
                "penalty_cap_applied",
                "calculation_branch",
                "base_rate_date",
                "ddu_signing_date",
                "acceptance_evasion",
                "unique_object",
                "deadline_confirmed",
            ):
                if name in calculation_columns:
                    batch.drop_column(name)

    intake_columns = _columns(bind, "calculation_intakes")
    if intake_columns:
        with op.batch_alter_table("calculation_intakes") as batch:
            for name in (
                "ddu_signing_date",
                "acceptance_evasion",
                "unique_object",
                "deadline_confirmed",
                "client_type",
            ):
                if name in intake_columns:
                    batch.drop_column(name)
