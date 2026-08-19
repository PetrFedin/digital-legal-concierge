"""Add client activity and structured Case closure/archive facts.

Revision ID: 20260819_0020
Revises: 20260819_0019
Create Date: 2026-08-19

Business closure, archive/read-only state and retention deletion are distinct
lifecycle facts. Client activity is stored separately from generic updated_at so
staff work cannot postpone abandoned-flow reminders.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260819_0020"
down_revision = "20260819_0019"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {
        item.get("name")
        for item in inspect(op.get_bind()).get_indexes(table)
        if item.get("name")
    }


def upgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())

    if "users" in tables:
        existing = _columns("users")
        with op.batch_alter_table("users") as batch:
            if "last_activity_at" not in existing:
                batch.add_column(
                    sa.Column(
                        "last_activity_at",
                        sa.DateTime(timezone=True),
                        nullable=True,
                    )
                )
        if "ix_users_last_activity_at" not in _indexes("users"):
            op.create_index(
                "ix_users_last_activity_at",
                "users",
                ["last_activity_at"],
                unique=False,
            )

    if "cases" in tables:
        existing = _columns("cases")
        with op.batch_alter_table("cases") as batch:
            if "last_client_action_at" not in existing:
                batch.add_column(
                    sa.Column(
                        "last_client_action_at",
                        sa.DateTime(timezone=True),
                        nullable=True,
                    )
                )
            if "close_reason" not in existing:
                batch.add_column(
                    sa.Column("close_reason", sa.String(length=100), nullable=True)
                )
            if "archived_at" not in existing:
                batch.add_column(
                    sa.Column(
                        "archived_at",
                        sa.DateTime(timezone=True),
                        nullable=True,
                    )
                )

        indexes = _indexes("cases")
        if "ix_cases_last_client_action_at" not in indexes:
            op.create_index(
                "ix_cases_last_client_action_at",
                "cases",
                ["last_client_action_at"],
                unique=False,
            )
        if "ix_cases_archived_at" not in indexes:
            op.create_index(
                "ix_cases_archived_at",
                "cases",
                ["archived_at"],
                unique=False,
            )

        # Historical ARCHIVED rows prove archive state but not exact archive time.
        # Use the best available legacy lifecycle timestamp and keep the semantic
        # distinction explicit in documentation; do not fabricate close reasons.
        op.execute(
            sa.text(
                """
                UPDATE cases
                SET archived_at = COALESCE(archived_at, closed_at, updated_at, created_at)
                WHERE status = 'ARCHIVED' AND archived_at IS NULL
                """
            )
        )


def downgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())

    if "cases" in tables:
        indexes = _indexes("cases")
        if "ix_cases_archived_at" in indexes:
            op.drop_index("ix_cases_archived_at", table_name="cases")
        if "ix_cases_last_client_action_at" in indexes:
            op.drop_index("ix_cases_last_client_action_at", table_name="cases")
        existing = _columns("cases")
        with op.batch_alter_table("cases") as batch:
            for name in ("archived_at", "close_reason", "last_client_action_at"):
                if name in existing:
                    batch.drop_column(name)

    if "users" in tables:
        indexes = _indexes("users")
        if "ix_users_last_activity_at" in indexes:
            op.drop_index("ix_users_last_activity_at", table_name="users")
        existing = _columns("users")
        with op.batch_alter_table("users") as batch:
            if "last_activity_at" in existing:
                batch.drop_column("last_activity_at")
