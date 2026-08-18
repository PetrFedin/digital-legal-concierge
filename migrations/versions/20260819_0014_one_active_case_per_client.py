"""Enforce one non-terminal case per client.

Revision ID: 20260819_0014
Revises: 20260806_0013
Create Date: 2026-08-19

The product has only one active client route at a time (M1 or M2). This partial
unique index keeps historical closed cases while preventing concurrent Telegram
or API requests from creating two live cases for the same client.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260819_0014"
down_revision = "20260806_0013"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_cases_one_active_per_client"
ACTIVE_PREDICATE = "status NOT IN ('M1_CLOSED', 'M2_CLOSED', 'ARCHIVED')"


def _duplicate_active_clients(bind) -> list[tuple[int, int]]:
    rows = bind.execute(
        sa.text(
            "SELECT client_id, COUNT(*) AS active_count "
            "FROM cases "
            f"WHERE {ACTIVE_PREDICATE} "
            "GROUP BY client_id "
            "HAVING COUNT(*) > 1 "
            "ORDER BY client_id "
            "LIMIT 50"
        )
    ).fetchall()
    return [(int(row[0]), int(row[1])) for row in rows]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "cases" not in set(inspector.get_table_names()):
        return

    duplicates = _duplicate_active_clients(bind)
    if duplicates:
        details = ", ".join(
            f"client_id={client_id}: {count} active cases"
            for client_id, count in duplicates
        )
        raise RuntimeError(
            "Cannot enforce one-active-case invariant while historical duplicates exist. "
            "Resolve the conflicting legal cases explicitly before migration; the migration "
            "will not auto-close or archive client matters. Conflicts: "
            + details
        )

    indexes = {index["name"] for index in inspect(bind).get_indexes("cases")}
    if INDEX_NAME in indexes:
        return

    dialect = bind.dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "One-active-case partial unique index requires PostgreSQL or SQLite; "
            f"current dialect is {dialect!r}."
        )

    kwargs = {}
    predicate = sa.text(ACTIVE_PREDICATE)
    if dialect == "postgresql":
        kwargs["postgresql_where"] = predicate
    else:
        kwargs["sqlite_where"] = predicate

    op.create_index(
        INDEX_NAME,
        "cases",
        ["client_id"],
        unique=True,
        **kwargs,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "cases" not in set(inspector.get_table_names()):
        return
    indexes = {index["name"] for index in inspector.get_indexes("cases")}
    if INDEX_NAME in indexes:
        op.drop_index(INDEX_NAME, table_name="cases")
