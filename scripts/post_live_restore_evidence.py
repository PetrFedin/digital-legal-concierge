from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.models.admin_user import AdminUser
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.document import Document
from app.models.payment import Payment
from app.models.payment_event import PaymentEvent
from app.security.audit_integrity import verify_audit_chain
from app.security.document_encryption import ENCRYPTION_STATUS, decrypt_file_bytes


SCHEMA_VERSION = 1
KIND_SNAPSHOT = "post_live_restore_snapshot"
KIND_VERIFICATION = "post_live_restore_verification"
_SAFE_TARGET_SUFFIXES = (
    "_staging",
    "_restore",
    "_drill",
    "_test",
    "-staging",
    "-restore",
    "-drill",
    "-test",
)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RestoreEvidenceError(RuntimeError):
    pass


def _utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _decimal(value: Decimal | object) -> str:
    return format(Decimal(str(value)), "f")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _hash_optional(value: object) -> str | None:
    normalized = str(value or "").strip()
    return _sha256_bytes(normalized.encode("utf-8")) if normalized else None


def _release_sha(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA_RE.fullmatch(normalized):
        raise RestoreEvidenceError("release SHA must be an exact 40-character commit SHA")
    return normalized


def _current_revision() -> str:
    try:
        return str(ScriptDirectory.from_config(Config("alembic.ini")).get_current_head())
    except Exception as error:
        raise RestoreEvidenceError("Unable to resolve current Alembic head") from error


async def _database_revision(db) -> str:  # noqa: ANN001
    try:
        value = await db.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
    except Exception as error:
        raise RestoreEvidenceError("Unable to read Alembic revision") from error
    normalized = str(value or "").strip()
    if not normalized:
        raise RestoreEvidenceError("Database has no Alembic revision")
    return normalized


def _database_identity() -> dict[str, str]:
    try:
        url = make_url(str(settings.database_url))
    except Exception as error:
        raise RestoreEvidenceError("DATABASE_URL is invalid") from error
    backend = url.get_backend_name()
    if backend not in {"postgresql", "postgres"}:
        raise RestoreEvidenceError("Post-LIVE restore evidence requires PostgreSQL")
    database = str(url.database or "").strip()
    if not database:
        raise RestoreEvidenceError("DATABASE_URL has no database name")
    endpoint = (
        f"{str(url.host or 'localhost').lower().rstrip('.')}:"
        f"{int(url.port or 5432)}:{database}"
    )
    return {
        "backend": "postgresql",
        "database": database,
        "database_sha256": _sha256_bytes(database.encode("utf-8")),
        "endpoint_sha256": _sha256_bytes(endpoint.encode("utf-8")),
    }


def _public_database_identity(identity: dict[str, str]) -> dict[str, str]:
    return {
        "backend": identity["backend"],
        "database_sha256": identity["database_sha256"],
        "endpoint_sha256": identity["endpoint_sha256"],
    }


def _safe_storage_root(value: Path) -> Path:
    candidate = Path(value)
    if candidate.is_symlink():
        raise RestoreEvidenceError("Storage root must not be a symbolic link")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_dir():
        raise RestoreEvidenceError("Storage root does not exist or is not a directory")
    return resolved


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _is_lexically_within(path: Path, root: Path) -> bool:
    try:
        _lexical_absolute(path).relative_to(root)
        return True
    except ValueError:
        return False


def _assert_no_symlink_components(root: Path, relative: Path) -> None:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RestoreEvidenceError("Symbolic links are forbidden in restore evidence storage paths")


def _relative_storage_path(path: Path, *, root: Path, error_message: str) -> Path:
    lexical = _lexical_absolute(path)
    try:
        relative = lexical.relative_to(root)
    except ValueError as error:
        raise RestoreEvidenceError(error_message) from error
    if not relative.parts:
        raise RestoreEvidenceError("Document path does not identify a file")
    _assert_no_symlink_components(root, relative)
    resolved = lexical.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise RestoreEvidenceError(error_message) from error
    return relative


def _source_storage_location(file_path: str, *, storage_root: Path) -> tuple[str, Path]:
    root = _safe_storage_root(storage_root)
    raw = str(file_path or "").strip()
    if not raw:
        raise RestoreEvidenceError("Document file_path is empty")
    candidate = Path(raw)
    if candidate.is_absolute():
        selected = candidate
    else:
        # Current writes persist an absolute path. This fallback keeps older
        # relative records usable while still requiring the selected location to
        # be lexically and physically inside the configured protected root.
        candidates = [root / candidate, Path.cwd() / candidate]
        selected = next(
            (
                item
                for item in candidates
                if item.exists() and _is_lexically_within(item, root)
            ),
            candidates[0],
        )
    relative = _relative_storage_path(
        selected,
        root=root,
        error_message="Document path is outside configured storage root",
    )
    resolved = _lexical_absolute(selected).resolve(strict=False)
    if not resolved.is_file():
        raise RestoreEvidenceError("Encrypted source document is missing or unsafe")
    return relative.as_posix(), resolved


def _restored_storage_path(relative_path: str, *, restored_storage_root: Path) -> Path:
    root = _safe_storage_root(restored_storage_root)
    raw = str(relative_path or "").strip()
    if not raw:
        raise RestoreEvidenceError("Snapshot document path is empty")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise RestoreEvidenceError("Snapshot document path is not a safe relative path")
    _assert_no_symlink_components(root, relative)
    candidate = (root / relative).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise RestoreEvidenceError("Snapshot document path escapes restored storage") from error
    if not candidate.is_file():
        raise RestoreEvidenceError("Restored encrypted document is missing or unsafe")
    return candidate


def _case_fact(case: Case) -> dict[str, object]:
    return {
        "id": int(case.id),
        "case_number_sha256": _hash_optional(case.case_number),
        "route": str(case.route) if case.route is not None else None,
        "status": str(case.status),
        "assigned_lawyer_id": (
            int(case.assigned_lawyer_id) if case.assigned_lawyer_id is not None else None
        ),
        "close_reason": case.close_reason,
        "closed_at": _utc(case.closed_at),
        "archived_at": _utc(case.archived_at),
        "content_deleted_at": _utc(case.content_deleted_at),
    }


def _payment_fact(payment: Payment) -> dict[str, object]:
    return {
        "id": int(payment.id),
        "case_id": int(payment.case_id),
        "payment_code_sha256": _hash_optional(payment.payment_code),
        "amount": _decimal(payment.amount),
        "currency": str(payment.currency),
        "status": str(payment.status),
        "provider": payment.provider,
        "provider_payment_id_sha256": _hash_optional(payment.provider_payment_id),
        "reservation_key_sha256": _hash_optional(payment.reservation_key),
        "paid_at": _utc(payment.paid_at),
        "failed_at": _utc(payment.failed_at),
        "cancelled_at": _utc(payment.cancelled_at),
        "refunded_at": _utc(payment.refunded_at),
        "expired_at": _utc(payment.expired_at),
    }


def _payment_event_fact(event: PaymentEvent) -> dict[str, object]:
    return {
        "id": int(event.id),
        "payment_id": int(event.payment_id),
        "case_id": int(event.case_id),
        "event_type": str(event.event_type),
        "status_before": event.status_before,
        "status_after": str(event.status_after),
        "payment_code_sha256": _hash_optional(event.payment_code),
        "amount": _decimal(event.amount),
        "currency": str(event.currency),
        "provider": event.provider,
        "provider_payment_id_sha256": _hash_optional(event.provider_payment_id),
        "reservation_key_sha256": _hash_optional(event.reservation_key),
        "source": str(event.source),
        "created_at": _utc(event.created_at),
    }


def _audit_fact(event: AuditLog) -> dict[str, object]:
    sensitive_payload_fingerprint = _sha256_json(
        {
            "old_value": event.old_value,
            "new_value": event.new_value,
            "comment": event.comment,
        }
    )
    return {
        "id": int(event.id),
        "actor_type": str(event.actor_type),
        "actor_id": int(event.actor_id) if event.actor_id is not None else None,
        "action": str(event.action),
        "entity_type": str(event.entity_type),
        "entity_id": int(event.entity_id) if event.entity_id is not None else None,
        "chain_sequence": (
            int(event.chain_sequence) if event.chain_sequence is not None else None
        ),
        "event_hash": event.event_hash,
        "integrity_key_id": event.integrity_key_id,
        "sealed_at": _utc(event.sealed_at),
        "created_at": _utc(event.created_at),
        "payload_sha256": sensitive_payload_fingerprint,
    }


def _audit_chain_fact(result: dict[str, Any]) -> dict[str, object]:
    return {
        "event_count": int(result["event_count"]),
        "checked_count": int(result["checked_count"]),
        "head_event_count": int(result["head_event_count"]),
        "head_hash": str(result["head_hash"]),
        "last_verified_hash": str(result["last_verified_hash"]),
        "head_matches": bool(result["head_matches"]),
        "key_ids": list(result["key_ids"]),
    }


def _staff_fact(account: AdminUser) -> dict[str, object]:
    return {
        "id": int(account.id),
        "username_sha256": _hash_optional(account.username),
        "role": str(account.role),
        "is_active": bool(account.is_active),
        "mfa_enabled": bool(account.mfa_enabled),
        "session_version": int(account.session_version or 1),
    }


async def _collect_facts(
    *,
    case_id: int,
    document_id: int,
    payment_id: int,
    staff_username: str,
    storage_root: Path,
    expected_storage_relative_path: str | None = None,
) -> dict[str, object]:
    async with AsyncSessionLocal() as db:
        revision = await _database_revision(db)
        expected_revision = _current_revision()
        if revision != expected_revision:
            raise RestoreEvidenceError(
                f"Database revision {revision!r} is not current head {expected_revision!r}"
            )

        audit_chain = await verify_audit_chain(db)
        if not bool(audit_chain.get("ok")):
            invalid = audit_chain.get("first_invalid") or {}
            raise RestoreEvidenceError(
                "Audit integrity chain is invalid: "
                + str(invalid.get("reason") or "unknown_integrity_failure")
            )

        case = await db.get(Case, int(case_id))
        if case is None:
            raise RestoreEvidenceError("Selected Case does not exist")
        document = await db.get(Document, int(document_id))
        if document is None or int(document.case_id) != int(case.id):
            raise RestoreEvidenceError("Selected Document does not belong to selected Case")
        payment = await db.get(Payment, int(payment_id))
        if payment is None or int(payment.case_id) != int(case.id):
            raise RestoreEvidenceError("Selected Payment does not belong to selected Case")

        username = str(staff_username or "").strip()
        if not username:
            raise RestoreEvidenceError("Selected staff username is empty")
        account = await db.scalar(select(AdminUser).where(AdminUser.username == username))
        if account is None:
            raise RestoreEvidenceError("Selected staff account does not exist")
        if not account.is_active:
            raise RestoreEvidenceError("Selected staff account is inactive")

        payment_events = list(
            (
                await db.execute(
                    select(PaymentEvent)
                    .where(PaymentEvent.payment_id == int(payment.id))
                    .order_by(PaymentEvent.id.asc())
                )
            ).scalars().all()
        )
        if not payment_events:
            raise RestoreEvidenceError("Selected Payment has no immutable PaymentEvent evidence")

        audit_events = list(
            (
                await db.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.entity_type == "case",
                        AuditLog.entity_id == int(case.id),
                    )
                    .order_by(AuditLog.id.asc())
                )
            ).scalars().all()
        )
        if not audit_events:
            raise RestoreEvidenceError("Selected Case has no AuditLog history")

        if str(document.encryption_status) != ENCRYPTION_STATUS:
            raise RestoreEvidenceError(
                "Selected historical Document is not encrypted; restore drill must prove decryption"
            )
        if document.data_key_destroyed_at is not None:
            raise RestoreEvidenceError("Selected Document data key has been destroyed")
        if not document.sha256:
            raise RestoreEvidenceError("Selected Document has no persisted SHA-256")

        if expected_storage_relative_path is None:
            relative_path, encrypted_path = _source_storage_location(
                str(document.file_path),
                storage_root=storage_root,
            )
        else:
            relative_path = str(expected_storage_relative_path)
            encrypted_path = _restored_storage_path(
                relative_path,
                restored_storage_root=storage_root,
            )

        plaintext, metadata = decrypt_file_bytes(
            encrypted_path,
            expected_sha256=document.sha256,
            encryption_key_id=document.encryption_key_id,
            encryption_envelope_id=document.encryption_envelope_id,
            encrypted_data_key=document.encrypted_data_key,
            encrypted_data_key_nonce=document.encrypted_data_key_nonce,
        )
        if not plaintext:
            raise RestoreEvidenceError("Selected historical Document decrypted to empty content")

        document_fact = {
            "id": int(document.id),
            "case_id": int(document.case_id),
            "document_type": str(document.document_type),
            "title_sha256": _hash_optional(document.title),
            "file_name_sha256": _hash_optional(document.file_name),
            "db_file_path_sha256": _hash_optional(document.file_path),
            "version": int(document.version),
            "status": str(document.status),
            "security_status": str(document.security_status),
            "encryption_status": str(document.encryption_status),
            "encryption_key_id": document.encryption_key_id,
            "encryption_format_version": int(document.encryption_format_version or 0),
            "encryption_envelope_id_sha256": _hash_optional(document.encryption_envelope_id),
            "sha256": str(document.sha256).lower(),
            "file_size": int(document.file_size) if document.file_size is not None else None,
            "storage_relative_path": relative_path,
            "decrypted_sha256": metadata.sha256,
            "decrypted_size": int(metadata.plaintext_size),
        }

        return {
            "database_revision": revision,
            "audit_chain": _audit_chain_fact(audit_chain),
            "case": _case_fact(case),
            "document": document_fact,
            "payment": _payment_fact(payment),
            "payment_events": [_payment_event_fact(item) for item in payment_events],
            "audit_events": [_audit_fact(item) for item in audit_events],
            "staff": _staff_fact(account),
        }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    if path.exists() or path.is_symlink():
        raise RestoreEvidenceError(f"Evidence output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def _read_snapshot(path: Path, *, expected_sha256: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RestoreEvidenceError("Source snapshot file is missing or unsafe")
    raw = path.read_bytes()
    actual = _sha256_bytes(raw)
    expected = str(expected_sha256 or "").strip().lower()
    if not _SHA256_RE.fullmatch(expected):
        raise RestoreEvidenceError("Expected source snapshot SHA-256 is invalid")
    if not hmac.compare_digest(actual, expected):
        raise RestoreEvidenceError("Source snapshot file SHA-256 does not match recorded value")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RestoreEvidenceError("Source snapshot JSON is invalid") from error
    if (
        payload.get("kind") != KIND_SNAPSHOT
        or int(payload.get("schema_version") or 0) != SCHEMA_VERSION
    ):
        raise RestoreEvidenceError("Source snapshot has unsupported schema")
    facts = payload.get("facts")
    if not isinstance(facts, dict):
        raise RestoreEvidenceError("Source snapshot has no facts object")
    actual_facts_sha = _sha256_json(facts)
    if not hmac.compare_digest(actual_facts_sha, str(payload.get("facts_sha256") or "")):
        raise RestoreEvidenceError("Source snapshot facts checksum is invalid")
    return payload


async def snapshot(args: argparse.Namespace) -> dict[str, object]:
    release_sha = _release_sha(args.release_sha)
    storage_root = Path(args.storage_dir or settings.storage_dir)
    facts = await _collect_facts(
        case_id=args.case_id,
        document_id=args.document_id,
        payment_id=args.payment_id,
        staff_username=args.staff_username,
        storage_root=storage_root,
    )
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_SNAPSHOT,
        "status": "SOURCE_SNAPSHOT_OK",
        "release_sha": release_sha,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "source_database": _public_database_identity(_database_identity()),
        "facts": facts,
        "facts_sha256": _sha256_json(facts),
    }
    output = Path(args.output)
    _write_json(output, payload)
    return {
        **payload,
        # The snapshot hash is intentionally not embedded into the snapshot itself.
        # Record this stdout value independently and require it for restore verify.
        "snapshot_file_sha256": _sha256_bytes(output.read_bytes()),
    }


async def verify(args: argparse.Namespace) -> dict[str, object]:
    release_sha = _release_sha(args.release_sha)
    snapshot_path = Path(args.snapshot)
    source = _read_snapshot(
        snapshot_path,
        expected_sha256=args.expected_snapshot_sha256,
    )
    if str(source.get("release_sha") or "") != release_sha:
        raise RestoreEvidenceError("Source snapshot belongs to a different release SHA")

    source_database = source.get("source_database")
    if not isinstance(source_database, dict):
        raise RestoreEvidenceError("Source snapshot has no database identity")
    target_database = _database_identity()
    if target_database["endpoint_sha256"] == source_database.get("endpoint_sha256"):
        raise RestoreEvidenceError("Restore verification refuses to run against source database endpoint")
    target_name = target_database["database"].lower()
    if not target_name.endswith(_SAFE_TARGET_SUFFIXES):
        raise RestoreEvidenceError(
            "Restore verification target database must end with staging, restore, drill or test"
        )

    source_facts = source["facts"]
    staff_username = str(args.staff_username or "").strip()
    expected_staff_hash = str(source_facts["staff"].get("username_sha256") or "")
    actual_staff_hash = _hash_optional(staff_username) or ""
    if not expected_staff_hash or not hmac.compare_digest(expected_staff_hash, actual_staff_hash):
        raise RestoreEvidenceError("Staff identity does not match source snapshot")

    restored_storage = Path(args.restored_storage_dir)
    target_facts = await _collect_facts(
        case_id=int(source_facts["case"]["id"]),
        document_id=int(source_facts["document"]["id"]),
        payment_id=int(source_facts["payment"]["id"]),
        staff_username=staff_username,
        storage_root=restored_storage,
        expected_storage_relative_path=str(
            source_facts["document"]["storage_relative_path"]
        ),
    )
    source_facts_sha = str(source.get("facts_sha256") or "")
    target_facts_sha = _sha256_json(target_facts)
    if not hmac.compare_digest(source_facts_sha, target_facts_sha):
        raise RestoreEvidenceError(
            "Restored application facts do not exactly match pre-backup source snapshot"
        )

    result: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_VERIFICATION,
        "status": "RESTORE_PASS",
        "release_sha": release_sha,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "source_snapshot_sha256": str(args.expected_snapshot_sha256).lower(),
        "facts_sha256": target_facts_sha,
        "target_database": _public_database_identity(target_database),
        "checks": {
            "alembic_current": True,
            "different_database_endpoint": True,
            "safe_restore_database_name": True,
            "audit_chain_valid": True,
            "case_exact_match": True,
            "historical_document_decryption": True,
            "payment_exact_match": True,
            "payment_event_ledger_exact_match": True,
            "case_audit_history_exact_match": True,
            "staff_account_exact_match": True,
            "restored_storage_file_verified": True,
        },
        "counts": {
            "audit_chain_events": int(target_facts["audit_chain"]["event_count"]),
            "payment_events": len(target_facts["payment_events"]),
            "case_audit_events": len(target_facts["audit_events"]),
        },
    }
    _write_json(Path(args.output), result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Two-phase application evidence for the post-LIVE encrypted backup restore drill"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    source = sub.add_parser(
        "snapshot",
        help="Capture privacy-minimized application facts before creating the encrypted backup",
    )
    source.add_argument("--release-sha", required=True)
    source.add_argument("--case-id", type=int, required=True)
    source.add_argument("--document-id", type=int, required=True)
    source.add_argument("--payment-id", type=int, required=True)
    source.add_argument("--staff-username", required=True)
    source.add_argument("--storage-dir", default=None)
    source.add_argument("--output", required=True)

    restored = sub.add_parser(
        "verify",
        help="Verify restored DB/storage against the exact pre-backup snapshot",
    )
    restored.add_argument("--release-sha", required=True)
    restored.add_argument("--snapshot", required=True)
    restored.add_argument("--expected-snapshot-sha256", required=True)
    restored.add_argument("--staff-username", required=True)
    restored.add_argument("--restored-storage-dir", required=True)
    restored.add_argument("--output", required=True)
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
    if args.command == "snapshot":
        return await snapshot(args)
    return await verify(args)


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(_run(args))
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, sort_keys=True))
        return 0
    except RestoreEvidenceError as error:
        print(
            json.dumps(
                {"ok": False, "error": str(error)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
