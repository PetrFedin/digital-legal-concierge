"""Add idempotent completion identity to durable calculator intake.

Revision ID: 20260916_0023
Revises: 20260915_0022
Create Date: 2026-09-16

A Telegram retry after the database commit must resolve to the already-created
Calculation instead of appending another legal result. The intake therefore
stores an explicit status/version and the exact completed Calculation id.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260916_0023"
down_revision = "20260915_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "calculation_intakes" not in inspector.get_table_names():
        return

    existing = {item["name"] for item in inspector.get_columns("calculation_intakes")}
    foreign_keys = inspector.get_foreign_keys("calculation_intakes")
    constrained = {
        tuple(item.get("constrained_columns") or [])
        for item in foreign_keys
    }

    with op.batch_alter_table("calculation_intakes") as batch:
        if "status" not in existing:
            batch.add_column(
                sa.Column(
                    "status",
                    sa.String(length=30),
                    nullable=False,
                    server_default="IN_PROGRESS",
                )
            )
        if "version" not in existing:
            batch.add_column(
                sa.Column("version", sa.Integer(), nullable=False, server_default="1")
            )
        if "completed_calculation_id" not in existing:
            batch.add_column(
                sa.Column("completed_calculation_id", sa.Integer(), nullable=True)
            )
        if ("completed_calculation_id",) not in constrained:
            batch.create_foreign_key(
                "fk_calculation_intakes_completed_calculation_id",
                "calculations",
                ["completed_calculation_id"],
                ["id"],
                ondelete="RESTRICT",
            )

    indexes = {
        item.get("name")
        for item in inspect(bind).get_indexes("calculation_intakes")
        if item.get("name")
    }
    if "ix_calculation_intakes_status" not in indexes:
        op.create_index(
            "ix_calculation_intakes_status",
            "calculation_intakes",
            ["status"],
            unique=False,
        )
    if "ix_calculation_intakes_completed_calculation_id" not in indexes:
        op.create_index(
            "ix_calculation_intakes_completed_calculation_id",
            "calculation_intakes",
            ["completed_calculation_id"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "calculation_intakes" not in inspector.get_table_names():
        return

    indexes = {
        item.get("name")
        for item in inspector.get_indexes("calculation_intakes")
        if item.get("name")
    }
    if "ix_calculation_intakes_completed_calculation_id" in indexes:
        op.drop_index(
            "ix_calculation_intakes_completed_calculation_id",
            table_name="calculation_intakes",
        )
    if "ix_calculation_intakes_status" in indexes:
        op.drop_index(
            "ix_calculation_intakes_status",
            table_name="calculation_intakes",
        )

    existing = {item["name"] for item in inspect(bind).get_columns("calculation_intakes")}
    foreign_keys = inspect(bind).get_foreign_keys("calculation_intakes")
    fk_names = {
        item.get("name")
        for item in foreign_keys
        if tuple(item.get("constrained_columns") or []) == ("completed_calculation_id",)
    }
    with op.batch_alter_table("calculation_intakes") as batch:
        for name in fk_names:
            if name:
                batch.drop_constraint(name, type_="foreignkey")
        for column in ("completed_calculation_id", "version", "status"):
            if column in existing:
                batch.drop_column(column)
