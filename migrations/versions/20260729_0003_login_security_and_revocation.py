"""Add persistent login throttle and access-token revocation.

Revision ID: 20260729_0003
Revises: 20260729_0002
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260729_0003"
down_revision = "20260729_0002"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    return table_name in inspect(bind).get_table_names()


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

    if not _table_exists(bind, "login_security_states"):
        op.create_table(
            "login_security_states",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("key_hash", sa.String(length=64), nullable=False),
            sa.Column(
                "failed_attempts",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "window_started_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "last_attempt_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "locked_until",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
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
        )

    security_indexes = _index_names(bind, "login_security_states")
    for index_name, columns, unique in [
        ("ix_login_security_states_key_hash", ["key_hash"], True),
        ("ix_login_security_states_last_attempt_at", ["last_attempt_at"], False),
        ("ix_login_security_states_locked_until", ["locked_until"], False),
    ]:
        if index_name not in security_indexes:
            op.create_index(
                index_name,
                "login_security_states",
                columns,
                unique=unique,
            )

    if not _table_exists(bind, "revoked_access_tokens"):
        op.create_table(
            "revoked_access_tokens",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("admin_users.id"),
                nullable=True,
            ),
            sa.Column(
                "expires_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.Column(
                "reason",
                sa.String(length=100),
                nullable=False,
                server_default=sa.text("'logout'"),
            ),
            sa.Column("comment", sa.Text(), nullable=True),
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
        )

    revoked_indexes = _index_names(bind, "revoked_access_tokens")
    for index_name, columns, unique in [
        ("ix_revoked_access_tokens_token_hash", ["token_hash"], True),
        ("ix_revoked_access_tokens_user_id", ["user_id"], False),
        ("ix_revoked_access_tokens_expires_at", ["expires_at"], False),
    ]:
        if index_name not in revoked_indexes:
            op.create_index(
                index_name,
                "revoked_access_tokens",
                columns,
                unique=unique,
            )


def downgrade() -> None:
    # Security evidence and active revocations must not be removed implicitly.
    pass
