"""Record the exact start of lawyer document review.

Revision ID: 20260806_0013
Revises: 20260805_0012
Create Date: 2026-08-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260806_0013"
down_revision = "20260805_0012"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_documents_review_started_at"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "documents" not in set(inspector.get_table_names()):
        return

    columns = {column["name"] for column in inspector.get_columns("documents")}
    if "review_started_at" not in columns:
        with op.batch_alter_table("documents") as batch:
            batch.add_column(
                sa.Column("review_started_at", sa.DateTime(timezone=True), nullable=True)
            )

    # Existing ON_REVIEW rows were previously timestamped only through
    # TimestampMixin.updated_at. Preserve that boundary instead of presenting
    # them as newly submitted after the migration.
    op.execute(
        sa.text(
            "UPDATE documents "
            "SET review_started_at = updated_at "
            "WHERE status = 'ON_REVIEW' "
            "AND review_started_at IS NULL"
        )
    )

    indexes = {index["name"] for index in inspect(bind).get_indexes("documents")}
    if INDEX_NAME not in indexes:
        op.create_index(
            INDEX_NAME,
            "documents",
            ["review_started_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "documents" not in set(inspector.get_table_names()):
        return

    indexes = {index["name"] for index in inspector.get_indexes("documents")}
    if INDEX_NAME in indexes:
        op.drop_index(INDEX_NAME, table_name="documents")

    columns = {column["name"] for column in inspect(bind).get_columns("documents")}
    if "review_started_at" in columns:
        with op.batch_alter_table("documents") as batch:
            batch.drop_column("review_started_at")
