"""Add source Telegram message idempotency.

Revision ID: 20260805_0012
Revises: 20260730_0011
Create Date: 2026-08-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260805_0012"
down_revision = "20260730_0011"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_messages_sender_source_message"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "messages" not in set(inspector.get_table_names()):
        return

    columns = {column["name"] for column in inspector.get_columns("messages")}
    if "source_message_id" not in columns:
        with op.batch_alter_table("messages") as batch:
            batch.add_column(
                sa.Column("source_message_id", sa.BigInteger(), nullable=True)
            )

    indexes = {index["name"] for index in inspect(bind).get_indexes("messages")}
    if INDEX_NAME not in indexes:
        op.create_index(
            INDEX_NAME,
            "messages",
            ["sender_type", "sender_id", "source_message_id"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "messages" not in set(inspector.get_table_names()):
        return

    indexes = {index["name"] for index in inspector.get_indexes("messages")}
    if INDEX_NAME in indexes:
        op.drop_index(INDEX_NAME, table_name="messages")

    columns = {column["name"] for column in inspect(bind).get_columns("messages")}
    if "source_message_id" in columns:
        with op.batch_alter_table("messages") as batch:
            batch.drop_column("source_message_id")
