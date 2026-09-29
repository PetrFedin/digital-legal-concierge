"""PM-027 customer contract: claim cutoff and four court deliverables.

Revision ID: 20260928_0027
Revises: 20260925_0026
Create Date: 2026-09-28

Additive only. Existing single-file package evidence remains readable while new
self-filing work is pinned to the four customer-approved court deliverables.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260928_0027"
down_revision = "20260925_0026"
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
    additions = (
        ("claim_source_calculation_id", sa.Integer(), sa.ForeignKey("calculations.id")),
        ("claim_calculation_cutoff_date", sa.Date(), None),
        ("claim_calculation_basis", sa.String(length=50), None),
        ("claim_update_in_court_required", sa.Boolean(), None),
        ("pretrial_claim_document_id", sa.Integer(), sa.ForeignKey("documents.id")),
        ("statement_of_claim_document_id", sa.Integer(), sa.ForeignKey("documents.id")),
        ("claim_calculation_document_id", sa.Integer(), sa.ForeignKey("documents.id")),
        ("client_roadmap_document_id", sa.Integer(), sa.ForeignKey("documents.id")),
    )
    with op.batch_alter_table("self_filing_packages") as batch:
        for name, type_, fk in additions:
            if name in cols:
                continue
            if fk is None:
                batch.add_column(sa.Column(name, type_, nullable=True))
            else:
                batch.add_column(sa.Column(name, type_, fk, nullable=True))

    indexes = _indexes(bind, "self_filing_packages")
    for name, column in (
        ("ix_self_filing_packages_claim_source_calculation_id", "claim_source_calculation_id"),
        ("ix_self_filing_packages_claim_calculation_cutoff_date", "claim_calculation_cutoff_date"),
        ("ix_self_filing_packages_pretrial_claim_document_id", "pretrial_claim_document_id"),
        ("ix_self_filing_packages_statement_of_claim_document_id", "statement_of_claim_document_id"),
        ("ix_self_filing_packages_claim_calculation_document_id", "claim_calculation_document_id"),
        ("ix_self_filing_packages_client_roadmap_document_id", "client_roadmap_document_id"),
    ):
        if name not in indexes:
            op.create_index(name, "self_filing_packages", [column], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if "self_filing_packages" not in set(inspect(bind).get_table_names()):
        return
    indexes = _indexes(bind, "self_filing_packages")
    for name in (
        "ix_self_filing_packages_client_roadmap_document_id",
        "ix_self_filing_packages_claim_calculation_document_id",
        "ix_self_filing_packages_statement_of_claim_document_id",
        "ix_self_filing_packages_pretrial_claim_document_id",
        "ix_self_filing_packages_claim_calculation_cutoff_date",
        "ix_self_filing_packages_claim_source_calculation_id",
    ):
        if name in indexes:
            op.drop_index(name, table_name="self_filing_packages")
    cols = _columns(bind, "self_filing_packages")
    with op.batch_alter_table("self_filing_packages") as batch:
        for name in (
            "client_roadmap_document_id",
            "claim_calculation_document_id",
            "statement_of_claim_document_id",
            "pretrial_claim_document_id",
            "claim_update_in_court_required",
            "claim_calculation_basis",
            "claim_calculation_cutoff_date",
            "claim_source_calculation_id",
        ):
            if name in cols:
                batch.drop_column(name)
