"""Add per-document envelope encryption metadata.

Revision ID: 20260730_0011
Revises: 20260730_0010
Create Date: 2026-07-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260730_0011"
down_revision = "20260730_0010"
branch_labels = None
depends_on = None


def _columns() -> dict[str, sa.Column]:
    return {
        "encryption_format_version": sa.Column(
            "encryption_format_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        "encryption_envelope_id": sa.Column(
            "encryption_envelope_id",
            sa.String(length=32),
            nullable=True,
        ),
        "encrypted_data_key": sa.Column(
            "encrypted_data_key",
            sa.Text(),
            nullable=True,
        ),
        "encrypted_data_key_nonce": sa.Column(
            "encrypted_data_key_nonce",
            sa.String(length=64),
            nullable=True,
        ),
        "data_key_destroyed_at": sa.Column(
            "data_key_destroyed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "documents" not in set(inspector.get_table_names()):
        return

    existing = {column["name"] for column in inspector.get_columns("documents")}
    with op.batch_alter_table("documents") as batch:
        for name, column in _columns().items():
            if name not in existing:
                batch.add_column(column)

    inspector = inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("documents")}
    if "ux_documents_encryption_envelope_id" not in indexes:
        op.create_index(
            "ux_documents_encryption_envelope_id",
            "documents",
            ["encryption_envelope_id"],
            unique=True,
        )
    if "ix_documents_data_key_destroyed_at" not in indexes:
        op.create_index(
            "ix_documents_data_key_destroyed_at",
            "documents",
            ["data_key_destroyed_at"],
            unique=False,
        )

    # Existing encrypted rows are DLCENC1 until the resumable background
    # migration verifies and rewrites each file into DLCENC2.
    op.execute(
        sa.text(
            "UPDATE documents SET encryption_format_version = 1 "
            "WHERE encryption_format_version IS NULL"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "documents" not in set(inspector.get_table_names()):
        return

    indexes = {index["name"] for index in inspector.get_indexes("documents")}
    if "ix_documents_data_key_destroyed_at" in indexes:
        op.drop_index("ix_documents_data_key_destroyed_at", table_name="documents")
    if "ux_documents_encryption_envelope_id" in indexes:
        op.drop_index("ux_documents_encryption_envelope_id", table_name="documents")

    existing = {column["name"] for column in inspect(bind).get_columns("documents")}
    with op.batch_alter_table("documents") as batch:
        for name in reversed(tuple(_columns())):
            if name in existing:
                batch.drop_column(name)
