"""Add Case transition authority recovery ledger and optimistic version.

Revision ID: 20260918_0024
Revises: 20260916_0023
Create Date: 2026-09-18

PM-018 makes Case process transitions recoverable after commit-before-response:
the Case carries a monotonic aggregate version, each applied transition has a
durable idempotency journal row, and one transactional outbox event is written
with the same commit as Case/Audit state.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260918_0024"
down_revision = "20260916_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())

    if "cases" in table_names:
        case_columns = {
            item["name"] for item in inspector.get_columns("cases")
        }
        if "version" not in case_columns:
            with op.batch_alter_table("cases") as batch:
                batch.add_column(
                    sa.Column(
                        "version",
                        sa.Integer(),
                        nullable=False,
                        server_default="1",
                    )
                )

    if "case_transition_commands" not in table_names:
        op.create_table(
            "case_transition_commands",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "case_id",
                sa.Integer(),
                sa.ForeignKey("cases.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "request_client_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="RESTRICT"),
                nullable=True,
            ),
            sa.Column("actor_type", sa.String(length=50), nullable=False),
            sa.Column("actor_id", sa.Integer(), nullable=True),
            sa.Column("action", sa.String(length=100), nullable=False),
            sa.Column("idempotency_key", sa.String(length=255), nullable=False),
            sa.Column("correlation_id", sa.String(length=255), nullable=True),
            sa.Column("expected_version", sa.Integer(), nullable=True),
            sa.Column("applied_version", sa.Integer(), nullable=False),
            sa.Column("source_status", sa.String(length=100), nullable=False),
            sa.Column("target_status", sa.String(length=100), nullable=False),
            sa.Column(
                "outcome",
                sa.String(length=30),
                nullable=False,
                server_default="APPLIED",
            ),
            sa.Column("result_payload", sa.JSON(), nullable=True),
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
                "case_id",
                "idempotency_key",
                name="uq_case_transition_commands_case_key",
            ),
        )
        op.create_index(
            "ix_case_transition_commands_case_id",
            "case_transition_commands",
            ["case_id"],
            unique=False,
        )
        op.create_index(
            "ix_case_transition_commands_request_client_id",
            "case_transition_commands",
            ["request_client_id"],
            unique=False,
        )
        op.create_index(
            "ix_case_transition_commands_action",
            "case_transition_commands",
            ["action"],
            unique=False,
        )
        op.create_index(
            "ix_case_transition_commands_correlation_id",
            "case_transition_commands",
            ["correlation_id"],
            unique=False,
        )
        op.create_index(
            "ix_case_transition_commands_outcome",
            "case_transition_commands",
            ["outcome"],
            unique=False,
        )

    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())
    if "case_transition_outbox_events" not in table_names:
        op.create_table(
            "case_transition_outbox_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "event_id",
                sa.String(length=36),
                nullable=False,
                unique=True,
            ),
            sa.Column(
                "case_id",
                sa.Integer(),
                sa.ForeignKey("cases.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "command_id",
                sa.Integer(),
                sa.ForeignKey(
                    "case_transition_commands.id",
                    ondelete="RESTRICT",
                ),
                nullable=False,
                unique=True,
            ),
            sa.Column("aggregate_version", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=100), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column(
                "status",
                sa.String(length=30),
                nullable=False,
                server_default="PENDING",
            ),
            sa.Column(
                "attempt_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column(
                "published_at",
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
        op.create_index(
            "ix_case_transition_outbox_events_case_id",
            "case_transition_outbox_events",
            ["case_id"],
            unique=False,
        )
        op.create_index(
            "ix_case_transition_outbox_events_aggregate_version",
            "case_transition_outbox_events",
            ["aggregate_version"],
            unique=False,
        )
        op.create_index(
            "ix_case_transition_outbox_events_event_type",
            "case_transition_outbox_events",
            ["event_type"],
            unique=False,
        )
        op.create_index(
            "ix_case_transition_outbox_events_status",
            "case_transition_outbox_events",
            ["status"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())

    if "case_transition_outbox_events" in table_names:
        op.drop_table("case_transition_outbox_events")

    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())
    if "case_transition_commands" in table_names:
        op.drop_table("case_transition_commands")

    inspector = inspect(bind)
    if "cases" in set(inspector.get_table_names()):
        case_columns = {
            item["name"] for item in inspector.get_columns("cases")
        }
        if "version" in case_columns:
            with op.batch_alter_table("cases") as batch:
                batch.drop_column("version")
