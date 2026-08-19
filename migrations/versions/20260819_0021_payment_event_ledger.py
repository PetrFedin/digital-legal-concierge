"""Add normalized append-only Payment event ledger.

Revision ID: 20260819_0021
Revises: 20260819_0020
Create Date: 2026-08-19

Provider webhook receipts and Case history remain authoritative evidence for
provider payload/provenance and business context. This table adds the missing
payment-centric invariant: every persisted Payment creation/status transition is
recorded as an immutable financial lifecycle snapshot.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260819_0021"
down_revision = "20260819_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "payment_events" not in tables:
        op.create_table(
            "payment_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("payment_id", sa.Integer(), nullable=False),
            sa.Column("case_id", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=100), nullable=False),
            sa.Column("status_before", sa.String(length=100), nullable=True),
            sa.Column("status_after", sa.String(length=100), nullable=False),
            sa.Column("payment_code", sa.String(length=100), nullable=False),
            sa.Column("amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("currency", sa.String(length=10), nullable=False),
            sa.Column("provider", sa.String(length=100), nullable=True),
            sa.Column("provider_payment_id", sa.String(length=255), nullable=True),
            sa.Column("reservation_key", sa.String(length=255), nullable=True),
            sa.Column(
                "source",
                sa.String(length=100),
                nullable=False,
                server_default="payment_model",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(
                ["case_id"],
                ["cases.id"],
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["payment_id"],
                ["payments.id"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        for name, columns in (
            ("ix_payment_events_payment_id", ["payment_id"]),
            ("ix_payment_events_case_id", ["case_id"]),
            ("ix_payment_events_event_type", ["event_type"]),
            ("ix_payment_events_status_after", ["status_after"]),
            ("ix_payment_events_created_at", ["created_at"]),
        ):
            op.create_index(name, "payment_events", columns, unique=False)

    # Existing rows predate the append-only hook. Seed one explicit baseline
    # event per Payment instead of inventing an unknown sequence of transitions.
    op.execute(
        sa.text(
            """
            INSERT INTO payment_events (
                payment_id, case_id, event_type, status_before, status_after,
                payment_code, amount, currency, provider,
                provider_payment_id, reservation_key, source, created_at
            )
            SELECT
                p.id, p.case_id, 'LEGACY_BASELINE', NULL, p.status,
                p.payment_code, p.amount, p.currency, p.provider,
                p.provider_payment_id, p.reservation_key,
                'migration_0021', COALESCE(p.created_at, CURRENT_TIMESTAMP)
            FROM payments p
            WHERE NOT EXISTS (
                SELECT 1 FROM payment_events e WHERE e.payment_id = p.id
            )
            """
        )
    )


def downgrade() -> None:
    if "payment_events" in set(inspect(op.get_bind()).get_table_names()):
        op.drop_table("payment_events")
