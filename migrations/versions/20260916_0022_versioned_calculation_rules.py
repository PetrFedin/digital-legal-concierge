"""Add versioned/effective-dated calculation rule evidence.

Revision ID: 20260916_0022
Revises: 20260819_0021
Create Date: 2026-09-16

PM-016 requires legal calculation inputs to come from controlled reference
rules instead of an environment-wide hardcoded rate. This migration creates
versioned rule directories and immutable calculation-segment evidence without
pretending that historical Calculation rows used rules that did not yet exist.
No legal rates, moratorium dates, coefficients or formulas are seeded here.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260916_0022"
down_revision = "20260819_0021"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def _create_date_rules(bind) -> None:
    if "calculation_date_rules" in _tables(bind):
        return
    op.create_table(
        "calculation_date_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_code", sa.String(length=100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("strategy_code", sa.String(length=100), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=True),
        sa.Column("source_reference", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_actor_type", sa.String(length=50), nullable=True),
        sa.Column("approved_by_actor_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_calc_date_rule_effective_range",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "rule_code", "revision", name="uq_calc_date_rule_revision"
        ),
    )
    for name, columns in (
        ("ix_calculation_date_rules_rule_code", ["rule_code"]),
        ("ix_calculation_date_rules_status", ["status"]),
        ("ix_calculation_date_rules_effective_from", ["effective_from"]),
        ("ix_calculation_date_rules_effective_to", ["effective_to"]),
    ):
        op.create_index(name, "calculation_date_rules", columns, unique=False)


def _create_client_type_rules(bind) -> None:
    if "calculation_client_type_rules" in _tables(bind):
        return
    op.create_table(
        "calculation_client_type_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_type_code", sa.String(length=100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("consumer_multiplier", sa.Numeric(12, 8), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_actor_type", sa.String(length=50), nullable=True),
        sa.Column("approved_by_actor_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_calc_client_type_rule_effective_range",
        ),
        sa.CheckConstraint(
            "consumer_multiplier > 0",
            name="ck_calc_client_type_multiplier_positive",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "client_type_code",
            "revision",
            name="uq_calc_client_type_rule_revision",
        ),
    )
    for name, columns in (
        ("ix_calc_client_type_rules_code", ["client_type_code"]),
        ("ix_calc_client_type_rules_status", ["status"]),
        ("ix_calc_client_type_rules_effective_from", ["effective_from"]),
        ("ix_calc_client_type_rules_effective_to", ["effective_to"]),
    ):
        op.create_index(name, "calculation_client_type_rules", columns, unique=False)


def _create_rule_sets(bind) -> None:
    if "calculation_rule_sets" in _tables(bind):
        return
    op.create_table(
        "calculation_rule_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("formula_code", sa.String(length=100), nullable=False),
        sa.Column("formula_parameters", sa.JSON(), nullable=True),
        sa.Column("rounding_code", sa.String(length=50), nullable=False),
        sa.Column("date_rule_id", sa.Integer(), nullable=True),
        sa.Column("client_type_rule_id", sa.Integer(), nullable=True),
        sa.Column("source_reference", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_actor_type", sa.String(length=50), nullable=True),
        sa.Column("approved_by_actor_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_calc_rule_set_effective_range",
        ),
        sa.ForeignKeyConstraint(
            ["date_rule_id"],
            ["calculation_date_rules.id"],
            name="fk_calc_rule_set_date_rule",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["client_type_rule_id"],
            ["calculation_client_type_rules.id"],
            name="fk_calc_rule_set_client_type_rule",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "revision", name="uq_calc_rule_set_revision"),
    )
    for name, columns in (
        ("ix_calculation_rule_sets_code", ["code"]),
        ("ix_calculation_rule_sets_status", ["status"]),
        ("ix_calculation_rule_sets_effective_from", ["effective_from"]),
        ("ix_calculation_rule_sets_effective_to", ["effective_to"]),
        ("ix_calculation_rule_sets_date_rule_id", ["date_rule_id"]),
        ("ix_calculation_rule_sets_client_type_rule_id", ["client_type_rule_id"]),
    ):
        op.create_index(name, "calculation_rule_sets", columns, unique=False)


def _create_rate_periods(bind) -> None:
    if "calculation_rate_periods" in _tables(bind):
        return
    op.create_table(
        "calculation_rate_periods",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_set_id", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("rate_code", sa.String(length=100), nullable=False),
        sa.Column("rate_value", sa.Numeric(12, 10), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_calc_rate_period_range",
        ),
        sa.CheckConstraint(
            "rate_value >= 0", name="ck_calc_rate_value_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["rule_set_id"],
            ["calculation_rule_sets.id"],
            name="fk_calc_rate_period_rule_set",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "rule_set_id",
            "valid_from",
            "valid_to",
            "rate_code",
            name="uq_calc_rate_period_identity",
        ),
    )
    for name, columns in (
        ("ix_calculation_rate_periods_rule_set_id", ["rule_set_id"]),
        ("ix_calculation_rate_periods_valid_from", ["valid_from"]),
        ("ix_calculation_rate_periods_valid_to", ["valid_to"]),
    ):
        op.create_index(name, "calculation_rate_periods", columns, unique=False)


def _create_exclusion_periods(bind) -> None:
    if "calculation_exclusion_periods" in _tables(bind):
        return
    op.create_table(
        "calculation_exclusion_periods",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_set_id", sa.Integer(), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("exclusion_type", sa.String(length=100), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "date_to >= date_from", name="ck_calc_exclusion_period_range"
        ),
        sa.ForeignKeyConstraint(
            ["rule_set_id"],
            ["calculation_rule_sets.id"],
            name="fk_calc_exclusion_period_rule_set",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "rule_set_id",
            "date_from",
            "date_to",
            "exclusion_type",
            name="uq_calc_exclusion_period_identity",
        ),
    )
    for name, columns in (
        ("ix_calculation_exclusion_periods_rule_set_id", ["rule_set_id"]),
        ("ix_calculation_exclusion_periods_date_from", ["date_from"]),
        ("ix_calculation_exclusion_periods_date_to", ["date_to"]),
    ):
        op.create_index(name, "calculation_exclusion_periods", columns, unique=False)


def _extend_calculations(bind) -> None:
    if "calculations" not in _tables(bind):
        return

    inspector = inspect(bind)
    existing = {item["name"] for item in inspector.get_columns("calculations")}
    additions = {
        "rule_set_id": sa.Column("rule_set_id", sa.Integer(), nullable=True),
        "rule_revision": sa.Column("rule_revision", sa.Integer(), nullable=True),
        "rule_snapshot_hash": sa.Column(
            "rule_snapshot_hash", sa.String(length=64), nullable=True
        ),
        "rule_snapshot_json": sa.Column("rule_snapshot_json", sa.JSON(), nullable=True),
        "calculation_end_date": sa.Column(
            "calculation_end_date", sa.Date(), nullable=True
        ),
        "delay_days_total": sa.Column("delay_days_total", sa.Integer(), nullable=True),
        "delay_days_chargeable": sa.Column(
            "delay_days_chargeable", sa.Integer(), nullable=True
        ),
        "moratorium_days": sa.Column("moratorium_days", sa.Integer(), nullable=True),
    }
    reflected_fks = {
        item.get("name")
        for item in inspector.get_foreign_keys("calculations")
        if item.get("name")
    }

    with op.batch_alter_table("calculations") as batch:
        for name, column in additions.items():
            if name not in existing:
                batch.add_column(column)
        if "fk_calculations_rule_set_id" not in reflected_fks:
            batch.create_foreign_key(
                "fk_calculations_rule_set_id",
                "calculation_rule_sets",
                ["rule_set_id"],
                ["id"],
                ondelete="RESTRICT",
            )

    indexes = {
        item.get("name")
        for item in inspect(bind).get_indexes("calculations")
        if item.get("name")
    }
    if "ix_calculations_rule_set_id" not in indexes:
        op.create_index(
            "ix_calculations_rule_set_id",
            "calculations",
            ["rule_set_id"],
            unique=False,
        )


def _create_segments(bind) -> None:
    if "calculation_segments" in _tables(bind):
        return
    op.create_table(
        "calculation_segments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("calculation_id", sa.Integer(), nullable=False),
        sa.Column("rule_set_id", sa.Integer(), nullable=False),
        sa.Column("rule_revision", sa.Integer(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("period_from", sa.Date(), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=False),
        sa.Column("days_total", sa.Integer(), nullable=False),
        sa.Column("days_excluded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("days_chargeable", sa.Integer(), nullable=False),
        sa.Column("rate_period_id", sa.Integer(), nullable=True),
        sa.Column("rate_value", sa.Numeric(12, 10), nullable=False),
        sa.Column("consumer_multiplier", sa.Numeric(12, 8), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("exclusion_evidence", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sequence_no > 0", name="ck_calculation_segment_sequence_positive"
        ),
        sa.CheckConstraint(
            "period_to >= period_from", name="ck_calculation_segment_range"
        ),
        sa.CheckConstraint(
            "days_total >= 0", name="ck_calculation_segment_days_total"
        ),
        sa.CheckConstraint(
            "days_excluded >= 0", name="ck_calculation_segment_days_excluded"
        ),
        sa.CheckConstraint(
            "days_chargeable >= 0", name="ck_calculation_segment_days_chargeable"
        ),
        sa.CheckConstraint(
            "days_chargeable + days_excluded = days_total",
            name="ck_calculation_segment_day_balance",
        ),
        sa.CheckConstraint(
            "amount >= 0", name="ck_calculation_segment_amount_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["calculation_id"],
            ["calculations.id"],
            name="fk_calculation_segment_calculation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rule_set_id"],
            ["calculation_rule_sets.id"],
            name="fk_calculation_segment_rule_set",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rate_period_id"],
            ["calculation_rate_periods.id"],
            name="fk_calculation_segment_rate_period",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "calculation_id",
            "sequence_no",
            name="uq_calculation_segment_sequence",
        ),
    )
    for name, columns in (
        ("ix_calculation_segments_calculation_id", ["calculation_id"]),
        ("ix_calculation_segments_rule_set_id", ["rule_set_id"]),
        ("ix_calculation_segments_rate_period_id", ["rate_period_id"]),
    ):
        op.create_index(name, "calculation_segments", columns, unique=False)


def upgrade() -> None:
    bind = op.get_bind()
    _create_date_rules(bind)
    _create_client_type_rules(bind)
    _create_rule_sets(bind)
    _create_rate_periods(bind)
    _create_exclusion_periods(bind)
    _extend_calculations(bind)
    _create_segments(bind)


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    if "calculation_segments" in tables:
        op.drop_table("calculation_segments")

    if "calculations" in tables:
        inspector = inspect(bind)
        columns = {item["name"] for item in inspector.get_columns("calculations")}
        indexes = {
            item.get("name")
            for item in inspector.get_indexes("calculations")
            if item.get("name")
        }
        fks = {
            item.get("name")
            for item in inspector.get_foreign_keys("calculations")
            if item.get("name")
        }
        if "ix_calculations_rule_set_id" in indexes:
            op.drop_index("ix_calculations_rule_set_id", table_name="calculations")
        with op.batch_alter_table("calculations") as batch:
            if "fk_calculations_rule_set_id" in fks:
                batch.drop_constraint("fk_calculations_rule_set_id", type_="foreignkey")
            for name in (
                "moratorium_days",
                "delay_days_chargeable",
                "delay_days_total",
                "calculation_end_date",
                "rule_snapshot_json",
                "rule_snapshot_hash",
                "rule_revision",
                "rule_set_id",
            ):
                if name in columns:
                    batch.drop_column(name)

    tables = _tables(bind)
    for table in (
        "calculation_exclusion_periods",
        "calculation_rate_periods",
        "calculation_rule_sets",
        "calculation_client_type_rules",
        "calculation_date_rules",
    ):
        if table in tables:
            op.drop_table(table)
