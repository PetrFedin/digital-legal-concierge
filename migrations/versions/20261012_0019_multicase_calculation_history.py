"""Client active-case selection and immutable case calculation history.

Revision ID: 20261012_0019
Revises: 20261011_0018
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261012_0019"
down_revision = "20261011_0018"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "users" in tables:
        cols = {c["name"] for c in insp.get_columns("users")}
        if "active_case_id" not in cols:
            with op.batch_alter_table("users") as batch:
                batch.add_column(sa.Column("active_case_id", sa.Integer(), nullable=True))
        indexes = {i["name"] for i in inspect(bind).get_indexes("users")}
        if "ix_users_active_case_id" not in indexes:
            op.create_index("ix_users_active_case_id", "users", ["active_case_id"])
    if "cases" in tables:
        cols = {c["name"] for c in inspect(bind).get_columns("cases")}
        if "creation_request_key" not in cols:
            with op.batch_alter_table("cases") as batch:
                batch.add_column(sa.Column("creation_request_key", sa.String(64), nullable=True))
        indexes = {i["name"] for i in inspect(bind).get_indexes("cases")}
        if "uq_cases_creation_request_key" not in indexes:
            op.create_index("uq_cases_creation_request_key", "cases", ["creation_request_key"], unique=True)
    if "calculations" in tables:
        for c in inspect(bind).get_unique_constraints("calculations"):
            if set(c.get("column_names") or []) == {"case_id"} and c.get("name"):
                with op.batch_alter_table("calculations") as batch:
                    batch.drop_constraint(c["name"], type_="unique")
        # Old unnamed SQLite and unique autoindex constraints are removed by
        # a controlled batch recreation only when the column remains unique.
        if bind.dialect.name == "sqlite":
            uniques = inspect(bind).get_indexes("calculations")
            if any(i.get("unique") and i.get("column_names") == ["case_id"] for i in uniques):
                with op.batch_alter_table("calculations", recreate="always") as batch:
                    pass
        indexes = {i["name"] for i in inspect(bind).get_indexes("calculations")}
        if "ix_calculations_case_id" not in indexes:
            op.create_index("ix_calculations_case_id", "calculations", ["case_id"])


def downgrade():
    raise RuntimeError(
        "Downgrade requires a reviewed data migration: several calculations may exist per case."
    )
