"""PM-027 self-filing court package service mode.

Revision ID: 20260925_0026
Revises: 20260924_0025
Create Date: 2026-09-25

Adds process/evidence storage only. It deliberately does not infer a court,
jurisdiction, Russian holiday calendar or email provider configuration.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260925_0026"
down_revision = "20260924_0025"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


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

    case_columns = _columns(bind, "cases")
    if case_columns and "service_mode" not in case_columns:
        op.add_column(
            "cases",
            sa.Column("service_mode", sa.String(length=50), nullable=True),
        )
    if "cases" in _tables(bind):
        indexes = _indexes(bind, "cases")
        if "ix_cases_service_mode" not in indexes:
            op.create_index(
                "ix_cases_service_mode",
                "cases",
                ["service_mode"],
                unique=False,
            )

    if "self_filing_packages" not in _tables(bind):
        op.create_table(
            "self_filing_packages",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "case_id",
                sa.Integer(),
                sa.ForeignKey("cases.id"),
                nullable=False,
            ),
            sa.Column(
                "status",
                sa.String(length=50),
                nullable=False,
                server_default="PROFILE_PENDING",
            ),
            sa.Column(
                "version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
            sa.Column("client_region", sa.String(length=255), nullable=True),
            sa.Column("client_address", sa.Text(), nullable=True),
            sa.Column("delivery_email", sa.String(length=320), nullable=True),
            sa.Column("email_confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "documents_complete_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "documents_complete_by_lawyer_id",
                sa.Integer(),
                sa.ForeignKey("lawyers.id"),
                nullable=True,
            ),
            sa.Column("court_name", sa.String(length=500), nullable=True),
            sa.Column("court_address", sa.Text(), nullable=True),
            sa.Column("jurisdiction_basis", sa.String(length=100), nullable=True),
            sa.Column(
                "jurisdiction_confirmed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "jurisdiction_confirmed_by_lawyer_id",
                sa.Integer(),
                sa.ForeignKey("lawyers.id"),
                nullable=True,
            ),
            sa.Column(
                "payment_confirmed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column("sla_started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "package_document_id",
                sa.Integer(),
                sa.ForeignKey("documents.id"),
                nullable=True,
            ),
            sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "email_delivery_status",
                sa.String(length=32),
                nullable=False,
                server_default="NOT_QUEUED",
            ),
            sa.Column(
                "email_delivery_attempts",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("email_message_id", sa.String(length=255), nullable=True),
            sa.Column("email_sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("email_last_error", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "case_id",
                name="uq_self_filing_packages_case_id",
            ),
        )
        for name, columns in (
            ("ix_self_filing_packages_case_id", ["case_id"]),
            ("ix_self_filing_packages_status", ["status"]),
            (
                "ix_self_filing_packages_documents_complete_at",
                ["documents_complete_at"],
            ),
            (
                "ix_self_filing_packages_documents_complete_by_lawyer_id",
                ["documents_complete_by_lawyer_id"],
            ),
            (
                "ix_self_filing_packages_jurisdiction_confirmed_at",
                ["jurisdiction_confirmed_at"],
            ),
            (
                "ix_self_filing_packages_jurisdiction_confirmed_by_lawyer_id",
                ["jurisdiction_confirmed_by_lawyer_id"],
            ),
            (
                "ix_self_filing_packages_payment_confirmed_at",
                ["payment_confirmed_at"],
            ),
            ("ix_self_filing_packages_sla_started_at", ["sla_started_at"]),
            ("ix_self_filing_packages_sla_due_at", ["sla_due_at"]),
            (
                "ix_self_filing_packages_package_document_id",
                ["package_document_id"],
            ),
            (
                "ix_self_filing_packages_email_delivery_status",
                ["email_delivery_status"],
            ),
            (
                "ix_self_filing_packages_email_sent_at",
                ["email_sent_at"],
            ),
        ):
            op.create_index(name, "self_filing_packages", columns, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if "self_filing_packages" in _tables(bind):
        op.drop_table("self_filing_packages")

    if "cases" in _tables(bind):
        indexes = _indexes(bind, "cases")
        if "ix_cases_service_mode" in indexes:
            op.drop_index("ix_cases_service_mode", table_name="cases")
        if "service_mode" in _columns(bind, "cases"):
            with op.batch_alter_table("cases") as batch:
                batch.drop_column("service_mode")
