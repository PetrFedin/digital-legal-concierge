"""Add short-lived one-time document access grants.

Revision ID: 20260729_0007
Revises: 20260729_0006
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260729_0007"
down_revision = "20260729_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "document_access_grants" in inspect(bind).get_table_names():
        return
    op.create_table(
        "document_access_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=32), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("actor_account_id", sa.Integer(), nullable=False),
        sa.Column("actor_role", sa.String(length=32), nullable=False),
        sa.Column("session_jti_ref", sa.String(length=80), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("token_key_id", sa.String(length=32), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False, server_default="download"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_ref", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(["actor_account_id"], ["admin_users.id"]),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("token_digest"),
    )
    for name, columns in [
        ("ix_document_access_grants_public_id", ["public_id"]),
        ("ix_document_access_grants_document_id", ["document_id"]),
        ("ix_document_access_grants_case_id", ["case_id"]),
        ("ix_document_access_grants_actor_account_id", ["actor_account_id"]),
        ("ix_document_access_grants_session_jti_ref", ["session_jti_ref"]),
        ("ix_document_access_grants_token_digest", ["token_digest"]),
        ("ix_document_access_grants_expires_at", ["expires_at"]),
        ("ix_document_access_grants_used_at", ["used_at"]),
        ("ix_document_access_grants_revoked_at", ["revoked_at"]),
        ("ix_document_access_grants_actor_active", ["actor_account_id", "expires_at"]),
        ("ix_document_access_grants_document_created", ["document_id", "created_at"]),
    ]:
        op.create_index(name, "document_access_grants", columns, unique=False)


def downgrade() -> None:
    op.drop_table("document_access_grants")
