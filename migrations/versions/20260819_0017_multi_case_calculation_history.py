"""Align Case and Calculation cardinality with the approved product model.

Revision ID: 20260819_0017
Revises: 20260819_0016
Create Date: 2026-08-19

A client may own several active legal matters. A Case has one active M1/M2 route
at a time, but may keep a history of several calculations. Duplicate Case
creation is deduplicated by the source operation, not by client_id.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260819_0017"
down_revision = "20260819_0016"
branch_labels = None
depends_on = None

INVALID_ACTIVE_CASE_INDEX = "uq_cases_one_active_per_client"
CALC_CASE_INDEX = "ix_calculations_case_id"
NAMING_CONVENTION = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def _table_names(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def _drop_invalid_client_index(bind) -> None:
    if "cases" not in _table_names(bind):
        return
    indexes = {
        item.get("name")
        for item in inspect(bind).get_indexes("cases")
        if item.get("name")
    }
    if INVALID_ACTIVE_CASE_INDEX in indexes:
        op.drop_index(INVALID_ACTIVE_CASE_INDEX, table_name="cases")


def _make_calculations_one_to_many(bind) -> None:
    if "calculations" not in _table_names(bind):
        return

    inspector = inspect(bind)
    unique_constraints = inspector.get_unique_constraints("calculations")
    case_unique = next(
        (
            item
            for item in unique_constraints
            if list(item.get("column_names") or []) == ["case_id"]
        ),
        None,
    )

    if case_unique is not None:
        constraint_name = case_unique.get("name") or "uq_calculations_case_id"
        with op.batch_alter_table(
            "calculations",
            naming_convention=NAMING_CONVENTION,
        ) as batch:
            batch.drop_constraint(constraint_name, type_="unique")

    # Some legacy schemas may represent UNIQUE(case_id) as a named unique
    # index rather than a reflected UniqueConstraint.
    inspector = inspect(bind)
    for item in inspector.get_indexes("calculations"):
        name = item.get("name")
        columns = list(item.get("column_names") or [])
        if (
            item.get("unique")
            and columns == ["case_id"]
            and name
            and name != CALC_CASE_INDEX
        ):
            op.drop_index(name, table_name="calculations")

    indexes = {
        item.get("name")
        for item in inspect(bind).get_indexes("calculations")
        if item.get("name")
    }
    if CALC_CASE_INDEX not in indexes:
        op.create_index(
            CALC_CASE_INDEX,
            "calculations",
            ["case_id"],
            unique=False,
        )


def _create_client_case_contexts(bind) -> None:
    if "client_case_contexts" in _table_names(bind):
        return
    op.create_table(
        "client_case_contexts",
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("selected_case_id", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["client_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["selected_case_id"], ["cases.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("client_id"),
    )
    op.create_index(
        "ix_client_case_contexts_selected_case_id",
        "client_case_contexts",
        ["selected_case_id"],
        unique=False,
    )


def _create_case_creation_requests(bind) -> None:
    if "case_creation_requests" in _table_names(bind):
        return
    op.create_table(
        "case_creation_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("operation_key", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.String(length=50), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(["client_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "client_id",
            "operation_key",
            name="uq_case_creation_client_operation",
        ),
    )
    op.create_index(
        "ix_case_creation_requests_client_id",
        "case_creation_requests",
        ["client_id"],
        unique=False,
    )
    op.create_index(
        "ix_case_creation_requests_case_id",
        "case_creation_requests",
        ["case_id"],
        unique=False,
    )


def upgrade() -> None:
    bind = op.get_bind()
    _drop_invalid_client_index(bind)
    _make_calculations_one_to_many(bind)
    _create_client_case_contexts(bind)
    _create_case_creation_requests(bind)


def downgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)
    if "case_creation_requests" in tables:
        op.drop_table("case_creation_requests")
    if "client_case_contexts" in tables:
        op.drop_table("client_case_contexts")

    # Intentionally do not restore the invalid client-wide Case uniqueness or
    # Calculation one-to-one constraint. Reintroducing either would reject
    # valid production data once several matters/calculations exist.
