"""Create structured court events required by the frozen lawyer UX.

Revision ID: 20261010_0017
Revises: 20261010_0016
Create Date: 2026-10-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261010_0017"
down_revision = "20261010_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "court_events" in set(inspector.get_table_names()):
        return
    op.create_table(
        "court_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("lawyer_id", sa.Integer(), sa.ForeignKey("lawyers.id"), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("court_name", sa.String(length=255), nullable=True),
        sa.Column("court_number", sa.String(length=255), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("client_comment", sa.Text(), nullable=True),
        sa.Column("attachments_note", sa.Text(), nullable=True),
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
    )
    op.create_index("ix_court_events_case_id", "court_events", ["case_id"])
    op.create_index("ix_court_events_lawyer_id", "court_events", ["lawyer_id"])
    op.create_index("ix_court_events_event_type", "court_events", ["event_type"])
    op.create_index("ix_court_events_event_date", "court_events", ["event_date"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "court_events" not in set(inspector.get_table_names()):
        return
    for name in (
        "ix_court_events_event_date",
        "ix_court_events_event_type",
        "ix_court_events_lawyer_id",
        "ix_court_events_case_id",
    ):
        op.drop_index(name, table_name="court_events")
    op.drop_table("court_events")
