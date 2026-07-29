"""Add document upload security metadata.

Revision ID: 20260729_0004
Revises: 20260729_0003
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260729_0004"
down_revision = "20260729_0003"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    return table_name in inspect(bind).get_table_names()


def _column_names(bind, table_name: str) -> set[str]:
    if not _table_exists(bind, table_name):
        return set()
    return {column["name"] for column in inspect(bind).get_columns(table_name)}


def _index_names(bind, table_name: str) -> set[str]:
    if not _table_exists(bind, table_name):
        return set()
    return {
        item["name"]
        for item in inspect(bind).get_indexes(table_name)
        if item.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "documents"):
        return

    columns = _column_names(bind, "documents")
    additions = [
        ("sha256", sa.Column("sha256", sa.String(length=64), nullable=True)),
        (
            "detected_type",
            sa.Column("detected_type", sa.String(length=32), nullable=True),
        ),
        (
            "security_status",
            sa.Column(
                "security_status",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'LEGACY_UNVERIFIED'"),
            ),
        ),
        (
            "security_reason",
            sa.Column("security_reason", sa.String(length=255), nullable=True),
        ),
        (
            "scanned_at",
            sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
        ),
    ]
    for name, column in additions:
        if name not in columns:
            op.add_column("documents", column)

    indexes = _index_names(bind, "documents")
    for index_name, index_columns in [
        ("ix_documents_sha256", ["sha256"]),
        ("ix_documents_security_status", ["security_status"]),
        ("ix_documents_case_sha256", ["case_id", "sha256"]),
    ]:
        if index_name not in indexes:
            op.create_index(
                index_name,
                "documents",
                index_columns,
                unique=False,
            )


def downgrade() -> None:
    # Security evidence must not be removed implicitly.
    pass
