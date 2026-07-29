"""Add superadmin MFA, recovery codes and session rotation.

Revision ID: 20260729_0002
Revises: 20260729_0001
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260729_0002"
down_revision = "20260729_0001"
branch_labels = None
depends_on = None


def _columns(bind) -> set[str]:
    if "admin_users" not in inspect(bind).get_table_names():
        return set()
    return {column["name"] for column in inspect(bind).get_columns("admin_users")}


def _indexes(bind) -> set[str]:
    if "admin_users" not in inspect(bind).get_table_names():
        return set()
    return {
        index["name"]
        for index in inspect(bind).get_indexes("admin_users")
        if index.get("name")
    }


def _ensure_column(bind, column: sa.Column) -> None:
    if column.name not in _columns(bind):
        op.add_column("admin_users", column)


def upgrade() -> None:
    bind = op.get_bind()
    if "admin_users" not in inspect(bind).get_table_names():
        return

    _ensure_column(
        bind,
        sa.Column(
            "mfa_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    _ensure_column(bind, sa.Column("mfa_secret_encrypted", sa.Text(), nullable=True))
    _ensure_column(
        bind,
        sa.Column("mfa_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    _ensure_column(bind, sa.Column("mfa_recovery_codes", sa.Text(), nullable=True))
    _ensure_column(
        bind,
        sa.Column(
            "mfa_recovery_codes_generated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    _ensure_column(
        bind,
        sa.Column(
            "mfa_failed_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    _ensure_column(
        bind,
        sa.Column("mfa_locked_until", sa.DateTime(timezone=True), nullable=True),
    )
    _ensure_column(
        bind,
        sa.Column("mfa_last_totp_step", sa.Integer(), nullable=True),
    )
    _ensure_column(
        bind,
        sa.Column(
            "session_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )

    bind.execute(
        sa.text(
            "UPDATE admin_users SET mfa_enabled=0 "
            "WHERE mfa_enabled IS NULL"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE admin_users SET mfa_failed_attempts=0 "
            "WHERE mfa_failed_attempts IS NULL"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE admin_users SET session_version=1 "
            "WHERE session_version IS NULL OR session_version < 1"
        )
    )

    indexes = _indexes(bind)
    if "ix_admin_users_mfa_enabled" not in indexes:
        op.create_index(
            "ix_admin_users_mfa_enabled",
            "admin_users",
            ["mfa_enabled"],
        )
    if "ix_admin_users_session_version" not in indexes:
        op.create_index(
            "ix_admin_users_session_version",
            "admin_users",
            ["session_version"],
        )


def downgrade() -> None:
    # MFA state is security-sensitive and may already be used by production
    # accounts. Automatic destructive downgrade is intentionally disabled.
    pass
