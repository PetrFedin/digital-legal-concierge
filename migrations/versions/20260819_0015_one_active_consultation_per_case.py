"""Enforce one non-terminal consultation per case.

Revision ID: 20260819_0015
Revises: 20260819_0014
Create Date: 2026-08-19

A case may retain any number of historical consultations, but only one live M2
appointment/intake context may own the current slot/payment/responsibility at a
time.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260819_0015"
down_revision = "20260819_0014"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_consultations_one_active_per_case"
ACTIVE_PREDICATE = (
    "status NOT IN ("
    "'DONE', 'CLIENT_NO_SHOW', 'LAWYER_NO_SHOW', "
    "'CANCELLED', 'RESCHEDULED', 'CLOSED'"
    ")"
)


def _duplicate_active_cases(bind) -> list[tuple[int, int]]:
    rows = bind.execute(
        sa.text(
            "SELECT case_id, COUNT(*) AS active_count "
            "FROM consultations "
            f"WHERE {ACTIVE_PREDICATE} "
            "GROUP BY case_id "
            "HAVING COUNT(*) > 1 "
            "ORDER BY case_id "
            "LIMIT 50"
        )
    ).fetchall()
    return [(int(row[0]), int(row[1])) for row in rows]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "consultations" not in set(inspector.get_table_names()):
        return

    duplicates = _duplicate_active_cases(bind)
    if duplicates:
        details = ", ".join(
            f"case_id={case_id}: {count} active consultations"
            for case_id, count in duplicates
        )
        raise RuntimeError(
            "Cannot enforce one-active-consultation invariant while historical conflicts exist. "
            "Resolve the current consultation/slot/payment ownership explicitly before migration; "
            "the migration will not cancel or close appointments automatically. Conflicts: "
            + details
        )

    indexes = {
        index["name"]
        for index in inspect(bind).get_indexes("consultations")
    }
    if INDEX_NAME in indexes:
        return

    dialect = bind.dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "One-active-consultation partial unique index requires PostgreSQL or SQLite; "
            f"current dialect is {dialect!r}."
        )

    predicate = sa.text(ACTIVE_PREDICATE)
    kwargs = (
        {"postgresql_where": predicate}
        if dialect == "postgresql"
        else {"sqlite_where": predicate}
    )
    op.create_index(
        INDEX_NAME,
        "consultations",
        ["case_id"],
        unique=True,
        **kwargs,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "consultations" not in set(inspector.get_table_names()):
        return
    indexes = {
        index["name"]
        for index in inspector.get_indexes("consultations")
    }
    if INDEX_NAME in indexes:
        op.drop_index(INDEX_NAME, table_name="consultations")
