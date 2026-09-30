"""Add staff access-management mutation version.

Revision ID: 20261001_0030
Revises: 20260928_0029
Create Date: 2026-10-01

session_version is authentication-token revocation authority. account_version is
separate optimistic-concurrency authority for superadmin edits so a stale staff
screen cannot overwrite a newer role/activity/MFA decision.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261001_0030"
down_revision = "20260928_0029"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    inspector = inspect(bind)
    if table not in set(inspector.get_table_names()):
        return set()
    return {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "admin_users" not in set(inspect(bind).get_table_names()):
        return
    cols = _columns(bind, "admin_users")
    if "account_version" in cols:
        return
    with op.batch_alter_table("admin_users") as batch:
        batch.add_column(
            sa.Column(
                "account_version",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "admin_users" not in set(inspect(bind).get_table_names()):
        return
    cols = _columns(bind, "admin_users")
    if "account_version" not in cols:
        return
    with op.batch_alter_table("admin_users") as batch:
        batch.drop_column("account_version")
