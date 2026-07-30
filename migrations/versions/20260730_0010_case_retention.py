"""Add controlled closed-case retention and legal hold.

Revision ID: 20260730_0010
Revises: 20260730_0009
Create Date: 2026-07-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260730_0010"
down_revision = "20260730_0009"
branch_labels = None
depends_on = None


def _case_columns() -> dict[str, sa.Column]:
    return {
        "closed_at": sa.Column(
            "closed_at", sa.DateTime(timezone=True), nullable=True
        ),
        "content_deleted_at": sa.Column(
            "content_deleted_at", sa.DateTime(timezone=True), nullable=True
        ),
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "cases" in tables:
        existing = {
            column["name"] for column in inspector.get_columns("cases")
        }
        with op.batch_alter_table("cases") as batch:
            for name, column in _case_columns().items():
                if name not in existing:
                    batch.add_column(column)
        inspector = inspect(bind)
        case_indexes = {index["name"] for index in inspector.get_indexes("cases")}
        if "ix_cases_closed_at" not in case_indexes:
            op.create_index("ix_cases_closed_at", "cases", ["closed_at"], unique=False)
        if "ix_cases_content_deleted_at" not in case_indexes:
            op.create_index(
                "ix_cases_content_deleted_at",
                "cases",
                ["content_deleted_at"],
                unique=False,
            )
        op.execute(
            sa.text(
                "UPDATE cases SET closed_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) "
                "WHERE closed_at IS NULL AND status IN "
                "('M1_CLOSED', 'M2_CLOSED', 'ARCHIVED')"
            )
        )

    if "case_retention_records" in tables:
        return

    op.create_table(
        "case_retention_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column(
            "policy_version",
            sa.String(length=50),
            nullable=False,
            server_default="case-content-v1",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="DISCOVERED",
        ),
        sa.Column("retention_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "legal_hold", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("legal_hold_reason", sa.Text(), nullable=True),
        sa.Column("legal_hold_set_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("legal_hold_set_by", sa.Integer(), nullable=True),
        sa.Column("legal_hold_released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("legal_hold_released_by", sa.Integer(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column("request_reason", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.Column("approval_comment", sa.Text(), nullable=True),
        sa.Column("execution_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("documents_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("messages_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notifications_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "consultations_anonymized", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("content_digest", sa.String(length=64), nullable=True),
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
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(["legal_hold_set_by"], ["admin_users.id"]),
        sa.ForeignKeyConstraint(["legal_hold_released_by"], ["admin_users.id"]),
        sa.ForeignKeyConstraint(["requested_by"], ["admin_users.id"]),
        sa.ForeignKeyConstraint(["approved_by"], ["admin_users.id"]),
        sa.UniqueConstraint("case_id", name="uq_case_retention_case"),
    )
    for name, columns in [
        ("ix_case_retention_records_case_id", ["case_id"]),
        ("ix_case_retention_records_status", ["status"]),
        ("ix_case_retention_records_retention_due_at", ["retention_due_at"]),
        ("ix_case_retention_records_legal_hold", ["legal_hold"]),
        ("ix_case_retention_status_due", ["status", "retention_due_at"]),
        ("ix_case_retention_hold_due", ["legal_hold", "retention_due_at"]),
    ]:
        op.create_index(name, "case_retention_records", columns, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "case_retention_records" in tables:
        op.drop_table("case_retention_records")
    if "cases" not in tables:
        return
    indexes = {index["name"] for index in inspect(bind).get_indexes("cases")}
    if "ix_cases_content_deleted_at" in indexes:
        op.drop_index("ix_cases_content_deleted_at", table_name="cases")
    if "ix_cases_closed_at" in indexes:
        op.drop_index("ix_cases_closed_at", table_name="cases")
    existing = {column["name"] for column in inspect(bind).get_columns("cases")}
    with op.batch_alter_table("cases") as batch:
        for name in reversed(tuple(_case_columns())):
            if name in existing:
                batch.drop_column(name)
