"""Retire the invalid one-active-case-per-client invariant.

Revision ID: 20260819_0014
Revises: 20260806_0013
Create Date: 2026-08-19

The approved product model allows one Client to own multiple Cases. BR-002
limits a single Case to one active route; it does not limit the client to one
live legal matter. This revision therefore must never create a client-wide
partial unique index.

For environments where an earlier unreleased copy of this revision created the
index before the correction was deployed, revision 0017 also removes it
defensively. Keeping the revision id preserves the Alembic chain without
reintroducing the wrong invariant on new databases.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "20260819_0014"
down_revision = "20260806_0013"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_cases_one_active_per_client"


def _drop_invalid_index_if_present() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "cases" not in set(inspector.get_table_names()):
        return
    indexes = {
        index.get("name")
        for index in inspector.get_indexes("cases")
        if index.get("name")
    }
    if INDEX_NAME in indexes:
        op.drop_index(INDEX_NAME, table_name="cases")


def upgrade() -> None:
    # New databases: no client-wide uniqueness is created.
    # A database running the corrected revision before Alembic records it also
    # gets any stray index removed.
    _drop_invalid_index_if_present()


def downgrade() -> None:
    # Do not recreate a product-invalid invariant during rollback.
    _drop_invalid_index_if_present()
