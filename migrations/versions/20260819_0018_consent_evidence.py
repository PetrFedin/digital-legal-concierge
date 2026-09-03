"""Persist immutable versioned personal-data consent evidence.

Revision ID: 20260819_0018
Revises: 20260819_0017
Create Date: 2026-08-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260819_0018"
down_revision = "20260819_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "consent_acceptances" in set(inspect(bind).get_table_names()):
        return

    op.create_table(
        "consent_acceptances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("consent_type", sa.String(length=50), nullable=False),
        sa.Column("consent_status", sa.String(length=20), nullable=False),
        sa.Column("consent_version", sa.String(length=100), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column("text_snapshot", sa.Text(), nullable=False),
        sa.Column("consent_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("source_message_id", sa.BigInteger(), nullable=True),
        sa.Column("source_callback_id", sa.String(length=255), nullable=False),
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
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "source_callback_id",
            name="uq_consent_user_callback",
        ),
    )
    op.create_index(
        "ix_consent_acceptances_case_id",
        "consent_acceptances",
        ["case_id"],
        unique=False,
    )
    op.create_index(
        "ix_consent_acceptances_user_id",
        "consent_acceptances",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_consent_acceptances_consent_date",
        "consent_acceptances",
        ["consent_date"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "consent_acceptances" in set(inspect(bind).get_table_names()):
        op.drop_table("consent_acceptances")
