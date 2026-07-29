"""Add tamper-evident HMAC chain to audit logs.

Revision ID: 20260729_0005
Revises: 20260729_0004
Create Date: 2026-07-29
"""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

from app.security.audit_integrity import (
    CHAIN_VERSION,
    GENESIS_HASH,
    audit_event_hash,
    canonical_audit_payload,
)
from app.security.keyring import audit_integrity_ring

revision = "20260729_0005"
down_revision = "20260729_0004"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    return table_name in inspect(bind).get_table_names()


def _column_names(bind, table_name: str) -> set[str]:
    if not _table_exists(bind, table_name):
        return set()
    return {column["name"] for column in inspect(bind).get_columns(table_name)}


def _index_names(bind, table_name: str) -> set[str]:
    if not _table_exists(bind, table_name):
        return set()
    return {
        item["name"]
        for item in inspect(bind).get_indexes(table_name)
        if item.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "audit_logs"):
        return

    columns = _column_names(bind, "audit_logs")
    additions = [
        (
            "chain_version",
            sa.Column("chain_version", sa.Integer(), nullable=True),
        ),
        (
            "chain_sequence",
            sa.Column("chain_sequence", sa.Integer(), nullable=True),
        ),
        (
            "previous_hash",
            sa.Column("previous_hash", sa.String(length=64), nullable=True),
        ),
        (
            "event_hash",
            sa.Column("event_hash", sa.String(length=64), nullable=True),
        ),
        (
            "integrity_key_id",
            sa.Column("integrity_key_id", sa.String(length=32), nullable=True),
        ),
        (
            "sealed_at",
            sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        ),
    ]
    for name, column in additions:
        if name not in columns:
            op.add_column("audit_logs", column)

    if not _table_exists(bind, "audit_chain_heads"):
        op.create_table(
            "audit_chain_heads",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "event_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "last_hash",
                sa.String(length=64),
                nullable=False,
                server_default=sa.text(f"'{GENESIS_HASH}'"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    metadata = sa.MetaData()
    audit_logs = sa.Table("audit_logs", metadata, autoload_with=bind)
    chain_heads = sa.Table("audit_chain_heads", metadata, autoload_with=bind)
    key = audit_integrity_ring().require_active()
    previous_hash = GENESIS_HASH
    sequence = 0
    sealed_at = datetime.now(timezone.utc)

    rows = bind.execute(
        sa.select(audit_logs).order_by(audit_logs.c.id.asc())
    ).mappings().all()
    for row in rows:
        sequence += 1
        created_at = row.get("created_at") or sealed_at
        payload = canonical_audit_payload(
            sequence=sequence,
            previous_hash=previous_hash,
            actor_type=row["actor_type"],
            actor_id=row.get("actor_id"),
            action=row["action"],
            entity_type=row["entity_type"],
            entity_id=row.get("entity_id"),
            old_value=row.get("old_value"),
            new_value=row.get("new_value"),
            comment=row.get("comment"),
            created_at=created_at,
        )
        event_hash = audit_event_hash(key, payload)
        bind.execute(
            audit_logs.update()
            .where(audit_logs.c.id == row["id"])
            .values(
                chain_version=CHAIN_VERSION,
                chain_sequence=sequence,
                previous_hash=previous_hash,
                event_hash=event_hash,
                integrity_key_id=key.key_id,
                sealed_at=sealed_at,
            )
        )
        previous_hash = event_hash

    bind.execute(chain_heads.delete())
    bind.execute(
        chain_heads.insert().values(
            id=1,
            event_count=sequence,
            last_hash=previous_hash,
            updated_at=sealed_at,
        )
    )

    indexes = _index_names(bind, "audit_logs")
    for index_name, columns_to_index in [
        ("ix_audit_logs_chain_sequence", ["chain_sequence"]),
        ("ix_audit_logs_event_hash", ["event_hash"]),
    ]:
        if index_name not in indexes:
            op.create_index(
                index_name,
                "audit_logs",
                columns_to_index,
                unique=True,
            )


def downgrade() -> None:
    # Audit integrity evidence must never be removed automatically.
    pass
