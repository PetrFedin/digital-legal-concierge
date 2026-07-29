"""Create or adopt the current application schema.

Revision ID: 20260729_0001
Revises:
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

from app.models import Base

revision = "20260729_0001"
down_revision = None
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    return table_name in inspect(bind).get_table_names()


def _column_names(bind, table_name: str) -> set[str]:
    if not _table_exists(bind, table_name):
        return set()
    return {
        column["name"]
        for column in inspect(bind).get_columns(table_name)
    }


def _index_names(bind, table_name: str) -> set[str]:
    if not _table_exists(bind, table_name):
        return set()
    return {
        index["name"]
        for index in inspect(bind).get_indexes(table_name)
        if index.get("name")
    }


def _ensure_column(bind, table_name: str, column: sa.Column) -> None:
    if table_name not in inspect(bind).get_table_names():
        return
    if column.name not in _column_names(bind, table_name):
        op.add_column(table_name, column)


def _ensure_index(
    bind,
    table_name: str,
    index_name: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> None:
    if not _table_exists(bind, table_name):
        return
    if index_name not in _index_names(bind, table_name):
        op.create_index(
            index_name,
            table_name,
            columns,
            unique=unique,
        )


def upgrade() -> None:
    bind = op.get_bind()

    # Fresh databases receive the complete schema. Existing databases keep all
    # rows and are extended below only where a legacy column or index is absent.
    Base.metadata.create_all(bind=bind)

    _ensure_column(
        bind,
        "admin_users",
        sa.Column("username", sa.String(length=100), nullable=True),
    )
    _ensure_column(
        bind,
        "admin_users",
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
    )

    _ensure_column(
        bind,
        "lawyers",
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
    )

    _ensure_column(
        bind,
        "cases",
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
    )
    _ensure_column(
        bind,
        "cases",
        sa.Column(
            "first_lawyer_response_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    _ensure_column(
        bind,
        "cases",
        sa.Column(
            "last_lawyer_activity_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    _ensure_column(
        bind,
        "cases",
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=True),
    )
    _ensure_column(
        bind,
        "cases",
        sa.Column(
            "sla_status",
            sa.String(length=50),
            nullable=False,
            server_default=sa.text("'NOT_STARTED'"),
        ),
    )
    _ensure_column(
        bind,
        "cases",
        sa.Column(
            "escalation_level",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    _ensure_column(
        bind,
        "consultations",
        sa.Column("related_case_id", sa.Integer(), nullable=True),
    )
    _ensure_column(
        bind,
        "consultations",
        sa.Column("slot_id", sa.Integer(), nullable=True),
    )
    _ensure_column(
        bind,
        "consultations",
        sa.Column(
            "subject_type",
            sa.String(length=50),
            nullable=False,
            server_default=sa.text("'new_or_other'"),
        ),
    )

    _ensure_column(
        bind,
        "payments",
        sa.Column("reservation_key", sa.String(length=255), nullable=True),
    )

    _ensure_column(
        bind,
        "notifications",
        sa.Column("recipient_type", sa.String(length=50), nullable=True),
    )
    _ensure_column(
        bind,
        "notifications",
        sa.Column("target_chat_id", sa.BigInteger(), nullable=True),
    )
    _ensure_column(
        bind,
        "notifications",
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
    )
    _ensure_column(
        bind,
        "notifications",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    _ensure_column(
        bind,
        "notifications",
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    _ensure_column(
        bind,
        "notifications",
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    _ensure_column(
        bind,
        "notifications",
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )

    if _table_exists(bind, "cases"):
        bind.execute(
            sa.text(
                "UPDATE cases SET sla_status='NOT_STARTED' "
                "WHERE sla_status IS NULL OR sla_status=''"
            )
        )
        bind.execute(
            sa.text(
                "UPDATE cases SET escalation_level=0 "
                "WHERE escalation_level IS NULL"
            )
        )
    if _table_exists(bind, "consultations"):
        bind.execute(
            sa.text(
                "UPDATE consultations SET subject_type='new_or_other' "
                "WHERE subject_type IS NULL OR subject_type=''"
            )
        )
    if _table_exists(bind, "notifications"):
        bind.execute(
            sa.text(
                "UPDATE notifications SET attempt_count=0 "
                "WHERE attempt_count IS NULL"
            )
        )

    _ensure_index(
        bind,
        "admin_users",
        "ix_admin_users_username",
        ["username"],
        unique=True,
    )
    _ensure_index(
        bind,
        "admin_users",
        "ix_admin_users_telegram_id",
        ["telegram_id"],
        unique=True,
    )
    _ensure_index(
        bind,
        "lawyers",
        "ix_lawyers_telegram_id",
        ["telegram_id"],
        unique=True,
    )
    _ensure_index(bind, "cases", "ix_cases_assigned_at", ["assigned_at"])
    _ensure_index(
        bind,
        "cases",
        "ix_cases_last_lawyer_activity_at",
        ["last_lawyer_activity_at"],
    )
    _ensure_index(bind, "cases", "ix_cases_sla_due_at", ["sla_due_at"])
    _ensure_index(bind, "cases", "ix_cases_sla_status", ["sla_status"])
    _ensure_index(
        bind,
        "payments",
        "ix_payments_reservation_key",
        ["reservation_key"],
    )
    _ensure_index(
        bind,
        "notifications",
        "ix_notifications_recipient_type",
        ["recipient_type"],
    )
    _ensure_index(
        bind,
        "notifications",
        "ix_notifications_target_chat_id",
        ["target_chat_id"],
    )
    _ensure_index(
        bind,
        "notifications",
        "ix_notifications_next_attempt_at",
        ["next_attempt_at"],
    )
    _ensure_index(
        bind,
        "notifications",
        "ix_notifications_dedupe_key",
        ["dedupe_key"],
        unique=True,
    )


def downgrade() -> None:
    # This is an adoption baseline for databases that may already contain
    # production data. It is intentionally irreversible to prevent accidental
    # table or column deletion. Future revisions must provide explicit,
    # independently reviewed downgrade logic where safe.
    pass
