"""Durable PM-027 SMTP delivery-attempt authority.

Revision ID: 20261001_0031
Revises: 20261001_0030
Create Date: 2026-10-01

Each SMTP attempt is committed before external I/O with the exact recipient,
Message-ID and four attachment ids/SHA-256 values. A crash after SMTP acceptance
can therefore be reconciled as UNKNOWN instead of blindly resent.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261001_0031"
down_revision = "20261001_0030"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def _indexes(bind, table: str) -> set[str]:
    inspector = inspect(bind)
    if table not in set(inspector.get_table_names()):
        return set()
    return {
        str(item.get("name"))
        for item in inspector.get_indexes(table)
        if item.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    if "self_filing_email_delivery_attempts" in _tables(bind):
        return

    op.create_table(
        "self_filing_email_delivery_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "package_id",
            sa.Integer(),
            sa.ForeignKey("self_filing_packages.id"),
            nullable=False,
        ),
        sa.Column(
            "case_id",
            sa.Integer(),
            sa.ForeignKey("cases.id"),
            nullable=False,
        ),
        sa.Column("package_version", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("recipient_email", sa.String(length=320), nullable=False),
        sa.Column("message_id", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("documents_snapshot", sa.JSON(), nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sending_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unknown_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("provider_receipt", sa.Text(), nullable=True),
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
            "package_id",
            "attempt_number",
            name="uq_self_filing_email_attempt_package_number",
        ),
    )

    for name, columns in (
        ("ix_self_filing_email_attempts_package_id", ["package_id"]),
        ("ix_self_filing_email_attempts_case_id", ["case_id"]),
        ("ix_self_filing_email_attempts_message_id", ["message_id"]),
        ("ix_self_filing_email_attempts_state", ["state"]),
        ("ix_self_filing_email_attempts_prepared_at", ["prepared_at"]),
        ("ix_self_filing_email_attempts_sending_at", ["sending_at"]),
        ("ix_self_filing_email_attempts_sent_at", ["sent_at"]),
        ("ix_self_filing_email_attempts_unknown_at", ["unknown_at"]),
    ):
        op.create_index(
            name,
            "self_filing_email_delivery_attempts",
            columns,
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "self_filing_email_delivery_attempts" in _tables(bind):
        op.drop_table("self_filing_email_delivery_attempts")
