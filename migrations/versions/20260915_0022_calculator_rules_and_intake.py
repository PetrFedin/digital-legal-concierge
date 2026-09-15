"""Add versioned calculator rules, durable intake and calculation evidence.

Revision ID: 20260915_0022
Revises: 20260819_0021
Create Date: 2026-09-15

The migration deliberately does not seed any legal rate, coefficient,
moratorium or exclusion period. A release must load a lawyer-approved rule
revision separately; inventing a legal rule in a schema migration would turn a
technical deployment into an unreviewed legal change.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260915_0022"
down_revision = "20260819_0021"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    if table not in _tables(bind):
        return set()
    return {item["name"] for item in inspect(bind).get_columns(table)}


def _indexes(bind, table: str) -> set[str]:
    if table not in _tables(bind):
        return set()
    return {
        str(item.get("name"))
        for item in inspect(bind).get_indexes(table)
        if item.get("name")
    }


def _create_rule_revisions(bind) -> None:
    if "calculation_rule_revisions" in _tables(bind):
        return
    op.create_table(
        "calculation_rule_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("revision_key", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("rules_sha256", sa.String(length=64), nullable=False),
        sa.Column("approved_by_actor_type", sa.String(length=50), nullable=True),
        sa.Column("approved_by_actor_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision_key", name="uq_calculation_rule_revisions_revision_key"),
    )
    for name, columns in (
        ("ix_calculation_rule_revisions_status", ["status"]),
        ("ix_calculation_rule_revisions_effective_from", ["effective_from"]),
        ("ix_calculation_rule_revisions_effective_to", ["effective_to"]),
        ("ix_calculation_rule_revisions_rules_sha256", ["rules_sha256"]),
        ("ix_calculation_rule_revisions_approved_at", ["approved_at"]),
    ):
        op.create_index(name, "calculation_rule_revisions", columns, unique=False)


def _create_intakes(bind) -> None:
    if "calculation_intakes" in _tables(bind):
        return
    op.create_table(
        "calculation_intakes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("contract_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("planned_transfer_date", sa.Date(), nullable=True),
        sa.Column("object_transferred", sa.Boolean(), nullable=True),
        sa.Column("actual_transfer_date", sa.Date(), nullable=True),
        sa.Column("calculation_date", sa.Date(), nullable=True),
        sa.Column(
            "current_step",
            sa.String(length=50),
            nullable=False,
            server_default="price",
        ),
        sa.Column(
            "source",
            sa.String(length=50),
            nullable=False,
            server_default="telegram_bot",
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_calculation_intakes_case_id",
        "calculation_intakes",
        ["case_id"],
        unique=True,
    )
    op.create_index(
        "ix_calculation_intakes_current_step",
        "calculation_intakes",
        ["current_step"],
        unique=False,
    )
    op.create_index(
        "ix_calculation_intakes_completed_at",
        "calculation_intakes",
        ["completed_at"],
        unique=False,
    )


def _extend_calculations(bind) -> None:
    if "calculations" not in _tables(bind):
        return
    existing = _columns(bind, "calculations")
    additions = (
        ("delay_days_total", sa.Integer()),
        ("delay_days_chargeable", sa.Integer()),
        ("moratorium_days", sa.Integer()),
        ("client_type", sa.String(length=50)),
        ("rule_revision_id", sa.Integer()),
        ("rule_revision_key", sa.String(length=100)),
        ("rule_snapshot_sha256", sa.String(length=64)),
        ("rule_snapshot", sa.JSON()),
        ("applied_segments", sa.JSON()),
    )
    for name, type_ in additions:
        if name not in existing:
            op.add_column("calculations", sa.Column(name, type_, nullable=True))

    # Existing consumer_multiplier used Numeric(5,2). Widening is safe and
    # avoids truncating an explicitly approved multiplier with more precision.
    reflected = {item["name"]: item for item in inspect(bind).get_columns("calculations")}
    multiplier = reflected.get("consumer_multiplier")
    if multiplier is not None:
        current_type = multiplier.get("type")
        precision = getattr(current_type, "precision", None)
        scale = getattr(current_type, "scale", None)
        if precision != 8 or scale != 4:
            with op.batch_alter_table("calculations") as batch:
                batch.alter_column(
                    "consumer_multiplier",
                    existing_type=current_type,
                    type_=sa.Numeric(8, 4),
                    existing_nullable=True,
                )

    # Add the FK after the target table exists. SQLite requires batch mode for
    # adding a named foreign key to an existing table.
    foreign_keys = inspect(bind).get_foreign_keys("calculations")
    has_rule_fk = any(
        list(item.get("constrained_columns") or []) == ["rule_revision_id"]
        for item in foreign_keys
    )
    if not has_rule_fk:
        with op.batch_alter_table("calculations") as batch:
            batch.create_foreign_key(
                "fk_calculations_rule_revision_id",
                "calculation_rule_revisions",
                ["rule_revision_id"],
                ["id"],
                ondelete="RESTRICT",
            )

    indexes = _indexes(bind, "calculations")
    for name, columns in (
        ("ix_calculations_rule_revision_id", ["rule_revision_id"]),
        ("ix_calculations_rule_revision_key", ["rule_revision_key"]),
        ("ix_calculations_rule_snapshot_sha256", ["rule_snapshot_sha256"]),
    ):
        if name not in indexes:
            op.create_index(name, "calculations", columns, unique=False)


def upgrade() -> None:
    bind = op.get_bind()
    _create_rule_revisions(bind)
    _create_intakes(bind)
    _extend_calculations(bind)


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "calculations" in tables:
        indexes = _indexes(bind, "calculations")
        for name in (
            "ix_calculations_rule_snapshot_sha256",
            "ix_calculations_rule_revision_key",
            "ix_calculations_rule_revision_id",
        ):
            if name in indexes:
                op.drop_index(name, table_name="calculations")
        existing = _columns(bind, "calculations")
        with op.batch_alter_table("calculations") as batch:
            for column in (
                "applied_segments",
                "rule_snapshot",
                "rule_snapshot_sha256",
                "rule_revision_key",
                "rule_revision_id",
                "client_type",
                "moratorium_days",
                "delay_days_chargeable",
                "delay_days_total",
            ):
                if column in existing:
                    batch.drop_column(column)

    if "calculation_intakes" in tables:
        op.drop_table("calculation_intakes")
    if "calculation_rule_revisions" in tables:
        op.drop_table("calculation_rule_revisions")
