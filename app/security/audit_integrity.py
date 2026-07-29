from __future__ import annotations

import hmac
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.security.keyring import KeyEntry, audit_integrity_ring, hmac_digest

GENESIS_HASH = "0" * 64
CHAIN_VERSION = 1
CHAIN_HEAD_ID = 1


class AuditIntegrityError(RuntimeError):
    pass


def _utc(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_datetime(value: datetime | str | None) -> str:
    return _utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, datetime):
        return canonical_datetime(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bytes):
        return value.hex()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def canonical_audit_payload(
    *,
    sequence: int,
    previous_hash: str,
    actor_type: str,
    actor_id: int | None,
    action: str,
    entity_type: str,
    entity_id: int | None,
    old_value: dict | None,
    new_value: dict | None,
    comment: str | None,
    created_at: datetime | str,
) -> bytes:
    payload = {
        "chain_version": CHAIN_VERSION,
        "sequence": int(sequence),
        "previous_hash": str(previous_hash),
        "actor_type": str(actor_type),
        "actor_id": actor_id,
        "action": str(action),
        "entity_type": str(entity_type),
        "entity_id": entity_id,
        "old_value": _canonical_value(old_value),
        "new_value": _canonical_value(new_value),
        "comment": comment,
        "created_at": canonical_datetime(created_at),
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def audit_event_hash(entry: KeyEntry, payload: bytes) -> str:
    return hmac_digest(entry, b"audit-chain-v1\x00" + payload)


def seal_audit_log(mapper, connection, target) -> None:
    from app.models.audit_chain_head import AuditChainHead

    head_table = AuditChainHead.__table__
    head = connection.execute(
        select(
            head_table.c.event_count,
            head_table.c.last_hash,
        )
        .where(head_table.c.id == CHAIN_HEAD_ID)
        .with_for_update()
    ).mappings().first()
    if head is None:
        connection.execute(
            insert(head_table).values(
                id=CHAIN_HEAD_ID,
                event_count=0,
                last_hash=GENESIS_HASH,
            )
        )
        event_count = 0
        previous_hash = GENESIS_HASH
    else:
        event_count = int(head["event_count"] or 0)
        previous_hash = str(head["last_hash"] or GENESIS_HASH)

    now = datetime.now(timezone.utc)
    created_at = _utc(getattr(target, "created_at", None) or now)
    sequence = event_count + 1
    key = audit_integrity_ring().require_active()
    payload = canonical_audit_payload(
        sequence=sequence,
        previous_hash=previous_hash,
        actor_type=target.actor_type,
        actor_id=target.actor_id,
        action=target.action,
        entity_type=target.entity_type,
        entity_id=target.entity_id,
        old_value=target.old_value,
        new_value=target.new_value,
        comment=target.comment,
        created_at=created_at,
    )
    event_hash = audit_event_hash(key, payload)

    target.created_at = created_at
    target.updated_at = created_at
    target.chain_version = CHAIN_VERSION
    target.chain_sequence = sequence
    target.previous_hash = previous_hash
    target.event_hash = event_hash
    target.integrity_key_id = key.key_id
    target.sealed_at = now

    connection.execute(
        update(head_table)
        .where(head_table.c.id == CHAIN_HEAD_ID)
        .values(
            event_count=sequence,
            last_hash=event_hash,
            updated_at=now,
        )
    )


def reject_audit_mutation(mapper, connection, target) -> None:
    raise AuditIntegrityError(
        "Записи аудита неизменяемы. Добавьте корректирующее событие вместо изменения."
    )


def register_audit_integrity_listeners(audit_log_model) -> None:
    from sqlalchemy import event

    if getattr(audit_log_model, "_integrity_listeners_registered", False):
        return
    event.listen(audit_log_model, "before_insert", seal_audit_log)
    event.listen(audit_log_model, "before_update", reject_audit_mutation)
    event.listen(audit_log_model, "before_delete", reject_audit_mutation)
    audit_log_model._integrity_listeners_registered = True


async def verify_audit_chain(db: AsyncSession) -> dict[str, Any]:
    from app.models.audit_chain_head import AuditChainHead
    from app.models.audit_log import AuditLog

    rows = (
        await db.execute(
            select(AuditLog).order_by(
                AuditLog.chain_sequence.asc(),
                AuditLog.id.asc(),
            )
        )
    ).scalars().all()
    head = await db.get(AuditChainHead, CHAIN_HEAD_ID)
    ring = audit_integrity_ring()

    expected_sequence = 1
    previous_hash = GENESIS_HASH
    checked = 0
    first_invalid: dict[str, Any] | None = None
    key_ids: set[str] = set()

    for row in rows:
        if row.chain_sequence is None or not row.event_hash or not row.integrity_key_id:
            first_invalid = {
                "id": row.id,
                "reason": "unsealed_event",
            }
            break
        if int(row.chain_version or 0) != CHAIN_VERSION:
            first_invalid = {
                "id": row.id,
                "reason": "unsupported_chain_version",
            }
            break
        if int(row.chain_sequence) != expected_sequence:
            first_invalid = {
                "id": row.id,
                "reason": "sequence_gap",
                "expected": expected_sequence,
                "actual": row.chain_sequence,
            }
            break
        if not hmac.compare_digest(str(row.previous_hash or ""), previous_hash):
            first_invalid = {
                "id": row.id,
                "reason": "previous_hash_mismatch",
            }
            break

        key = ring.by_id(row.integrity_key_id)
        if key is None:
            first_invalid = {
                "id": row.id,
                "reason": "verification_key_missing",
                "key_id": row.integrity_key_id,
            }
            break
        payload = canonical_audit_payload(
            sequence=row.chain_sequence,
            previous_hash=row.previous_hash,
            actor_type=row.actor_type,
            actor_id=row.actor_id,
            action=row.action,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            old_value=row.old_value,
            new_value=row.new_value,
            comment=row.comment,
            created_at=row.created_at,
        )
        expected_hash = audit_event_hash(key, payload)
        if not hmac.compare_digest(expected_hash, row.event_hash):
            first_invalid = {
                "id": row.id,
                "reason": "event_hash_mismatch",
            }
            break

        checked += 1
        expected_sequence += 1
        previous_hash = row.event_hash
        key_ids.add(row.integrity_key_id)

    head_count = int(head.event_count or 0) if head else 0
    head_hash = str(head.last_hash or GENESIS_HASH) if head else GENESIS_HASH
    head_matches = (
        first_invalid is None
        and head_count == len(rows)
        and hmac.compare_digest(head_hash, previous_hash)
    )
    if first_invalid is None and not head_matches:
        first_invalid = {
            "id": None,
            "reason": "chain_head_mismatch",
            "head_count": head_count,
            "actual_count": len(rows),
        }

    return {
        "ok": first_invalid is None,
        "event_count": len(rows),
        "checked_count": checked,
        "head_event_count": head_count,
        "head_hash": head_hash,
        "last_verified_hash": previous_hash,
        "head_matches": head_matches,
        "key_ids": sorted(key_ids),
        "first_invalid": first_invalid,
    }
