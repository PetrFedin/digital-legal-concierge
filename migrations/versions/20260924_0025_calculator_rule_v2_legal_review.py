"""PM-016 v2 legal review lifecycle and calculation evidence.

Revision ID: 20260924_0025
Revises: 20260918_0024
Create Date: 2026-09-24

This migration adds workflow/evidence fields only. It deliberately does not seed
legal rates, caps, moratoria or other legal truth. Production authority must be
created as DRAFT, legally reviewed against its exact SHA-256, approved and then
published through the controlled rule editor.
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
    if table not in set(inspector.get_table_names()):
        return set()
    return {item["name"] for item in inspector.get_columns(table)}


def _indexes(bind, table: str) -> set[str]:
    inspector = inspect(bind)
    if table not in set(inspector.get_table_names()):
        return set()
    return {
        str(item.get("name"))
        for item in inspector.get_indexes(table)
        if item.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()

    rule_columns = _columns(bind, "calculation_rule_revisions")
    if rule_columns:
        additions = (
            ("legal_reviewed_by_actor_type", sa.String(length=50)),
            ("legal_reviewed_by_actor_id", sa.Integer()),
            ("legal_reviewed_at", sa.DateTime(timezone=True)),
            ("legal_review_sha256", sa.String(length=64)),
            ("legal_review_comment", sa.Text()),
            ("published_by_actor_type", sa.String(length=50)),
            ("published_by_actor_id", sa.Integer()),
            ("published_at", sa.DateTime(timezone=True)),
        )
        for name, type_ in additions:
            if name not in rule_columns:
                op.add_column(
                    "calculation_rule_revisions",
                    sa.Column(name, type_, nullable=True),
                )
        existing_indexes = _indexes(bind, "calculation_rule_revisions")
        for name, columns in (
            ("ix_calculation_rule_revisions_legal_reviewed_at", ["legal_reviewed_at"]),
            ("ix_calculation_rule_revisions_legal_review_sha256", ["legal_review_sha256"]),
            ("ix_calculation_rule_revisions_published_at", ["published_at"]),
        ):
            if name not in existing_indexes:
                op.create_index(
                    name,
                    "calculation_rule_revisions",
                    columns,
                    unique=False,
                )

    intake_columns = _columns(bind, "calculation_intakes")
    if intake_columns:
        for name, type_ in (
            ("client_type", sa.String(length=50)),
            ("unique_object", sa.Boolean()),
            ("manual_review_flags", sa.JSON()),
        ):
            if name not in intake_columns:
                op.add_column(
                    "calculation_intakes",
                    sa.Column(name, type_, nullable=True),
                )

    calculation_columns = _columns(bind, "calculations")
    if calculation_columns:
        for name, type_ in (
            ("unique_object", sa.Boolean()),
            ("gross_penalty_amount", sa.Numeric(14, 2)),
            ("amount_cap", sa.Numeric(14, 2)),
            ("amount_cap_applied", sa.Boolean()),
            ("manual_review_required", sa.Boolean()),
            ("manual_review_reasons", sa.JSON()),
            ("excluded_segments", sa.JSON()),
        ):
            if name not in calculation_columns:
                op.add_column(
                    "calculations",
                    sa.Column(name, type_, nullable=True),
                )


def downgrade() -> None:
    bind = op.get_bind()

    calculation_columns = _columns(bind, "calculations")
    if calculation_columns:
        with op.batch_alter_table("calculations") as batch:
            for column in (
                "excluded_segments",
                "manual_review_reasons",
                "manual_review_required",
                "amount_cap_applied",
                "amount_cap",
                "gross_penalty_amount",
                "unique_object",
            ):
                if column in calculation_columns:
                    batch.drop_column(column)

    intake_columns = _columns(bind, "calculation_intakes")
    if intake_columns:
        with op.batch_alter_table("calculation_intakes") as batch:
            for column in (
                "manual_review_flags",
                "unique_object",
                "client_type",
            ):
                if column in intake_columns:
                    batch.drop_column(column)

    rule_columns = _columns(bind, "calculation_rule_revisions")
    if rule_columns:
        indexes = _indexes(bind, "calculation_rule_revisions")
        for name in (
            "ix_calculation_rule_revisions_published_at",
            "ix_calculation_rule_revisions_legal_review_sha256",
            "ix_calculation_rule_revisions_legal_reviewed_at",
        ):
            if name in indexes:
                op.drop_index(name, table_name="calculation_rule_revisions")
        with op.batch_alter_table("calculation_rule_revisions") as batch:
            for column in (
                "published_at",
                "published_by_actor_id",
                "published_by_actor_type",
                "legal_review_comment",
                "legal_review_sha256",
                "legal_reviewed_at",
                "legal_reviewed_by_actor_id",
                "legal_reviewed_by_actor_type",
            ):
                if column in rule_columns:
                    batch.drop_column(column)
