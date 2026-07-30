"""Add per-document envelope encryption metadata.

Revision ID: 20260730_0011
Revises: 20260730_0010
Create Date: 2026-07-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260730_0011"
down_revision = "20260730_0010"
branch_labels = None
depends_on = None

SQLITE_RETENTION_TRIGGER = "trg_retention_destroy_document_keys"
POSTGRES_RETENTION_FUNCTION = "destroy_document_envelope_keys_on_retention"


def _columns() -> dict[str, sa.Column]:
    return {
        "encryption_format_version": sa.Column(
            "encryption_format_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        "encryption_envelope_id": sa.Column(
            "encryption_envelope_id",
            sa.String(length=32),
            nullable=True,
        ),
        "encrypted_data_key": sa.Column(
            "encrypted_data_key",
            sa.Text(),
            nullable=True,
        ),
        "encrypted_data_key_nonce": sa.Column(
            "encrypted_data_key_nonce",
            sa.String(length=64),
            nullable=True,
        ),
        "data_key_destroyed_at": sa.Column(
            "data_key_destroyed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    }


def _install_retention_key_destruction(bind) -> None:
    tables = set(inspect(bind).get_table_names())
    if "case_retention_records" not in tables:
        return
    dialect = bind.dialect.name
    if dialect == "sqlite":
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER IF NOT EXISTS {SQLITE_RETENTION_TRIGGER}
                AFTER UPDATE OF status ON case_retention_records
                WHEN NEW.status = 'EXECUTING' AND OLD.status <> 'EXECUTING'
                BEGIN
                    UPDATE documents
                    SET encrypted_data_key = NULL,
                        encrypted_data_key_nonce = NULL,
                        data_key_destroyed_at = COALESCE(
                            data_key_destroyed_at,
                            CURRENT_TIMESTAMP
                        )
                    WHERE case_id = NEW.case_id
                      AND encryption_format_version = 2
                      AND data_key_destroyed_at IS NULL;
                END
                """
            )
        )
        return
    if dialect == "postgresql":
        op.execute(
            sa.text(
                f"""
                CREATE OR REPLACE FUNCTION {POSTGRES_RETENTION_FUNCTION}()
                RETURNS trigger AS $$
                BEGIN
                    IF NEW.status = 'EXECUTING'
                       AND OLD.status IS DISTINCT FROM 'EXECUTING' THEN
                        UPDATE documents
                        SET encrypted_data_key = NULL,
                            encrypted_data_key_nonce = NULL,
                            data_key_destroyed_at = COALESCE(
                                data_key_destroyed_at,
                                CURRENT_TIMESTAMP
                            )
                        WHERE case_id = NEW.case_id
                          AND encryption_format_version = 2
                          AND data_key_destroyed_at IS NULL;
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
                """
            )
        )
        op.execute(
            sa.text(
                f"""
                DROP TRIGGER IF EXISTS {SQLITE_RETENTION_TRIGGER}
                ON case_retention_records
                """
            )
        )
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER {SQLITE_RETENTION_TRIGGER}
                AFTER UPDATE OF status ON case_retention_records
                FOR EACH ROW
                EXECUTE FUNCTION {POSTGRES_RETENTION_FUNCTION}()
                """
            )
        )
        return
    raise RuntimeError(
        "Envelope key destruction requires SQLite or PostgreSQL retention trigger support"
    )


def _drop_retention_key_destruction(bind) -> None:
    dialect = bind.dialect.name
    if dialect == "sqlite":
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {SQLITE_RETENTION_TRIGGER}"))
    elif dialect == "postgresql":
        op.execute(
            sa.text(
                f"""
                DROP TRIGGER IF EXISTS {SQLITE_RETENTION_TRIGGER}
                ON case_retention_records
                """
            )
        )
        op.execute(
            sa.text(
                f"DROP FUNCTION IF EXISTS {POSTGRES_RETENTION_FUNCTION}()"
            )
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "documents" not in set(inspector.get_table_names()):
        return

    existing = {column["name"] for column in inspector.get_columns("documents")}
    with op.batch_alter_table("documents") as batch:
        for name, column in _columns().items():
            if name not in existing:
                batch.add_column(column)

    inspector = inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("documents")}
    if "ux_documents_encryption_envelope_id" not in indexes:
        op.create_index(
            "ux_documents_encryption_envelope_id",
            "documents",
            ["encryption_envelope_id"],
            unique=True,
        )
    if "ix_documents_data_key_destroyed_at" not in indexes:
        op.create_index(
            "ix_documents_data_key_destroyed_at",
            "documents",
            ["data_key_destroyed_at"],
            unique=False,
        )

    # Existing encrypted rows are DLCENC1 until the resumable background
    # migration verifies and rewrites each file into DLCENC2.
    op.execute(
        sa.text(
            "UPDATE documents SET encryption_format_version = 1 "
            "WHERE encryption_format_version IS NULL"
        )
    )
    _install_retention_key_destruction(bind)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "documents" not in set(inspector.get_table_names()):
        return

    _drop_retention_key_destruction(bind)
    indexes = {index["name"] for index in inspector.get_indexes("documents")}
    if "ix_documents_data_key_destroyed_at" in indexes:
        op.drop_index("ix_documents_data_key_destroyed_at", table_name="documents")
    if "ux_documents_encryption_envelope_id" in indexes:
        op.drop_index("ux_documents_encryption_envelope_id", table_name="documents")

    existing = {column["name"] for column in inspect(bind).get_columns("documents")}
    with op.batch_alter_table("documents") as batch:
        for name in reversed(tuple(_columns())):
            if name in existing:
                batch.drop_column(name)
