"""DLC-INT-01 encrypted PDF/OCR derivative authority.

Revision ID: 20261001_0032
Revises: 20261001_0031
Create Date: 2026-10-01

Derived PDF/OCR artifacts are separate from the immutable source Document and
from the business document-version/review workflow. Each derivative records its
source checksum, processor/version, lineage and DLCENC2 envelope metadata.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261001_0032"
down_revision = "20261001_0031"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    if "document_derivatives" in _tables(bind):
        return

    op.create_table(
        "document_derivatives",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column(
            "source_document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id"),
            nullable=False,
        ),
        sa.Column("derivative_type", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=True),
        sa.Column(
            "mime_type",
            sa.String(length=100),
            nullable=False,
            server_default=sa.text("'application/pdf'"),
        ),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("tool_version", sa.String(length=64), nullable=False),
        sa.Column("recipe_id", sa.String(length=128), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("has_usable_text", sa.Boolean(), nullable=True),
        sa.Column(
            "encryption_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column("encryption_key_id", sa.String(length=32), nullable=True),
        sa.Column(
            "encryption_format_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("2"),
        ),
        sa.Column("encryption_envelope_id", sa.String(length=32), nullable=True),
        sa.Column("encrypted_data_key", sa.Text(), nullable=True),
        sa.Column("encrypted_data_key_nonce", sa.String(length=64), nullable=True),
        sa.Column("data_key_destroyed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("encrypted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=255), nullable=True),
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
        sa.UniqueConstraint(
            "source_document_id",
            "derivative_type",
            "source_sha256",
            "tool_name",
            "tool_version",
            "recipe_id",
            name="uq_document_derivative_reproducible_identity",
        ),
    )

    for name, columns, unique in (
        ("ix_document_derivatives_case_id", ["case_id"], False),
        ("ix_document_derivatives_source_document_id", ["source_document_id"], False),
        ("ix_document_derivatives_derivative_type", ["derivative_type"], False),
        ("ix_document_derivatives_status", ["status"], False),
        ("ix_document_derivatives_source_sha256", ["source_sha256"], False),
        ("ix_document_derivatives_sha256", ["sha256"], False),
        ("ix_document_derivatives_encryption_key_id", ["encryption_key_id"], False),
        ("ix_document_derivatives_data_key_destroyed_at", ["data_key_destroyed_at"], False),
        (
            "ux_document_derivatives_encryption_envelope_id",
            ["encryption_envelope_id"],
            True,
        ),
        (
            "ix_document_derivatives_source_type",
            ["source_document_id", "derivative_type"],
            False,
        ),
        (
            "ix_document_derivatives_case_status",
            ["case_id", "status"],
            False,
        ),
    ):
        op.create_index(name, "document_derivatives", columns, unique=unique)


def downgrade() -> None:
    bind = op.get_bind()
    if "document_derivatives" in _tables(bind):
        op.drop_table("document_derivatives")
