"""Enforce one active payment attempt per case stage and provider identity.

Revision ID: 20260819_0016
Revises: 20260819_0015
Create Date: 2026-08-19

Application-level Case locking serializes normal payment creation. These partial
unique indexes are the final database backstop for concurrent retries and any
future code path that bypasses PaymentService.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260819_0016"
down_revision = "20260819_0015"
branch_labels = None
depends_on = None

ACTIVE_INDEX = "uq_payments_one_active_attempt_per_case_code"
PROVIDER_INDEX = "uq_payments_provider_operation"
ACTIVE_PREDICATE = "status IN ('PENDING', 'WAITING_CONFIRMATION')"
PROVIDER_PREDICATE = "provider_payment_id IS NOT NULL"


def _active_duplicates(bind) -> list[tuple[int, str, int]]:
    rows = bind.execute(
        sa.text(
            "SELECT case_id, payment_code, COUNT(*) AS active_count "
            "FROM payments "
            f"WHERE {ACTIVE_PREDICATE} "
            "GROUP BY case_id, payment_code "
            "HAVING COUNT(*) > 1 "
            "ORDER BY case_id, payment_code "
            "LIMIT 50"
        )
    ).fetchall()
    return [(int(row[0]), str(row[1]), int(row[2])) for row in rows]


def _provider_duplicates(bind) -> list[tuple[str, str, int]]:
    rows = bind.execute(
        sa.text(
            "SELECT COALESCE(provider, ''), provider_payment_id, COUNT(*) AS duplicate_count "
            "FROM payments "
            f"WHERE {PROVIDER_PREDICATE} "
            "GROUP BY COALESCE(provider, ''), provider_payment_id "
            "HAVING COUNT(*) > 1 "
            "ORDER BY provider_payment_id "
            "LIMIT 50"
        )
    ).fetchall()
    return [(str(row[0]), str(row[1]), int(row[2])) for row in rows]


def _where_kwargs(dialect: str, predicate: str) -> dict:
    clause = sa.text(predicate)
    if dialect == "postgresql":
        return {"postgresql_where": clause}
    if dialect == "sqlite":
        return {"sqlite_where": clause}
    raise RuntimeError(
        "Payment partial unique indexes require PostgreSQL or SQLite; "
        f"current dialect is {dialect!r}."
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "payments" not in set(inspector.get_table_names()):
        return

    active_duplicates = _active_duplicates(bind)
    if active_duplicates:
        details = ", ".join(
            f"case_id={case_id} code={code}: {count} active attempts"
            for case_id, code, count in active_duplicates
        )
        raise RuntimeError(
            "Cannot enforce active-payment idempotency while duplicate live links exist. "
            "Reconcile each payment with provider state before migration; the migration will "
            "not expire, cancel, refund or choose a payment automatically. Conflicts: "
            + details
        )

    provider_duplicates = _provider_duplicates(bind)
    if provider_duplicates:
        details = ", ".join(
            f"provider={provider or '<empty>'} id={provider_id}: {count} rows"
            for provider, provider_id, count in provider_duplicates
        )
        raise RuntimeError(
            "Cannot enforce provider-operation identity while multiple internal payments share "
            "one provider_payment_id. Reconcile them against provider records explicitly; no "
            "payment rows are rewritten automatically. Conflicts: "
            + details
        )

    indexes = {index["name"] for index in inspect(bind).get_indexes("payments")}
    dialect = bind.dialect.name

    if ACTIVE_INDEX not in indexes:
        op.create_index(
            ACTIVE_INDEX,
            "payments",
            ["case_id", "payment_code"],
            unique=True,
            **_where_kwargs(dialect, ACTIVE_PREDICATE),
        )

    if PROVIDER_INDEX not in indexes:
        op.create_index(
            PROVIDER_INDEX,
            "payments",
            ["provider", "provider_payment_id"],
            unique=True,
            **_where_kwargs(dialect, PROVIDER_PREDICATE),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "payments" not in set(inspector.get_table_names()):
        return
    indexes = {index["name"] for index in inspector.get_indexes("payments")}
    if PROVIDER_INDEX in indexes:
        op.drop_index(PROVIDER_INDEX, table_name="payments")
    if ACTIVE_INDEX in indexes:
        op.drop_index(ACTIVE_INDEX, table_name="payments")
