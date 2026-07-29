"""Add idempotent payment webhook event ledger.

Revision ID: 20260729_0008
Revises: 20260729_0007
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260729_0008"
down_revision = "20260729_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "payment_webhook_events" in inspect(bind).get_table_names():
        return
    op.create_table(
        "payment_webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("event_key", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=255), nullable=True),
        sa.Column("payment_id", sa.Integer(), nullable=True),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_summary", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PROCESSING"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"]),
        sa.UniqueConstraint("provider", "event_key", name="uq_payment_webhook_provider_event"),
    )
    for name, columns in [
        ("ix_payment_webhook_events_provider", ["provider"]),
        ("ix_payment_webhook_events_event_key", ["event_key"]),
        ("ix_payment_webhook_events_event_type", ["event_type"]),
        ("ix_payment_webhook_events_provider_payment_id", ["provider_payment_id"]),
        ("ix_payment_webhook_events_payment_id", ["payment_id"]),
        ("ix_payment_webhook_events_status", ["status"]),
        ("ix_payment_webhook_status_last_seen", ["status", "last_seen_at"]),
        ("ix_payment_webhook_payment_created", ["payment_id", "created_at"]),
    ]:
        op.create_index(name, "payment_webhook_events", columns, unique=False)


def downgrade() -> None:
    op.drop_table("payment_webhook_events")
