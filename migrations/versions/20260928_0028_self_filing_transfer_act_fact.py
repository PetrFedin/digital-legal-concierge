"""PM-027 lawyer-confirmed transfer-act cutoff authority.

Revision ID: 20260928_0028
Revises: 20260928_0027
Create Date: 2026-09-28

The act fact is confirmed by the responsible lawyer before payment is opened.
It prevents a stale preliminary calculator answer from selecting the cutoff.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260928_0028"
down_revision = "20260928_0027"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    inspector = inspect(bind)
    if table not in set(inspector.get_table_names()):
        return set()
    return {item["name"] for item in inspector.get_columns(table)}


def _indexes(bind, table: str) -> set[str]:
    inspector = inspect(bind)
    if table not in set(inspector.get_table_names()):
        return set()
    return {
        str(item.get("name"))
        for item in inspector.get_indexes(table)
        if item.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    if "self_filing_packages" not in set(inspect(bind).get_table_names()):
        return

    cols = _columns(bind, "self_filing_packages")
    with op.batch_alter_table("self_filing_packages") as batch:
        if "transfer_act_signed" not in cols:
            batch.add_column(sa.Column("transfer_act_signed", sa.Boolean(), nullable=True))
        if "transfer_act_date" not in cols:
            batch.add_column(sa.Column("transfer_act_date", sa.Date(), nullable=True))
        if "transfer_act_confirmed_at" not in cols:
            batch.add_column(
                sa.Column(
                    "transfer_act_confirmed_at",
                    sa.DateTime(timezone=True),
                    nullable=True,
                )
            )
        if "transfer_act_confirmed_by_lawyer_id" not in cols:
            batch.add_column(
                sa.Column(
                    "transfer_act_confirmed_by_lawyer_id",
                    sa.Integer(),
                    sa.ForeignKey("lawyers.id"),
                    nullable=True,
                )
            )

    indexes = _indexes(bind, "self_filing_packages")
    for name, column in (
        ("ix_self_filing_packages_transfer_act_date", "transfer_act_date"),
        ("ix_self_filing_packages_transfer_act_confirmed_at", "transfer_act_confirmed_at"),
        (
            "ix_self_filing_packages_transfer_act_confirmed_by_lawyer_id",
            "transfer_act_confirmed_by_lawyer_id",
        ),
    ):
        if name not in indexes:
            op.create_index(name, "self_filing_packages", [column], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if "self_filing_packages" not in set(inspect(bind).get_table_names()):
        return

    indexes = _indexes(bind, "self_filing_packages")
    for name in (
        "ix_self_filing_packages_transfer_act_confirmed_by_lawyer_id",
        "ix_self_filing_packages_transfer_act_confirmed_at",
        "ix_self_filing_packages_transfer_act_date",
    ):
        if name in indexes:
            op.drop_index(name, table_name="self_filing_packages")

    cols = _columns(bind, "self_filing_packages")
    with op.batch_alter_table("self_filing_packages") as batch:
        for name in (
            "transfer_act_confirmed_by_lawyer_id",
            "transfer_act_confirmed_at",
            "transfer_act_date",
            "transfer_act_signed",
        ):
            if name in cols:
                batch.drop_column(name)
