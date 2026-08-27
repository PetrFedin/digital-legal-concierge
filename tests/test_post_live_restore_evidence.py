from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import post_live_restore_evidence as evidence


SHA = "a" * 40
STAFF_USERNAME = "restore-verifier"


def _facts(*, staff_username: str = STAFF_USERNAME) -> dict[str, object]:
    return {
        "database_revision": "rev-1",
        "audit_chain": {
            "event_count": 7,
            "checked_count": 7,
            "head_event_count": 7,
            "head_hash": "b" * 64,
            "last_verified_hash": "b" * 64,
            "head_matches": True,
            "key_ids": ["audit-v1"],
        },
        "case": {"id": 11, "status": "M2_CONSULTATION_BOOKED"},
        "document": {
            "id": 22,
            "storage_relative_path": "cases/11/document.dlcenc",
        },
        "payment": {"id": 33, "status": "PAID"},
        "payment_events": [{"id": 44, "status_after": "PAID"}],
        "audit_events": [{"id": 55, "action": "payment_paid"}],
        "staff": {
            "id": 66,
            "username_sha256": evidence._hash_optional(staff_username),
            "role": "admin",
        },
    }


def _write_snapshot(
    path: Path,
    *,
    facts: dict[str, object] | None = None,
    source_endpoint: str = "source-endpoint",
) -> str:
    selected = facts or _facts()
    payload = {
        "schema_version": evidence.SCHEMA_VERSION,
        "kind": evidence.KIND_SNAPSHOT,
        "status": "SOURCE_SNAPSHOT_OK",
        "release_sha": SHA,
        "recorded_at": "2026-08-27T00:00:00+00:00",
        "source_database": {
            "backend": "postgresql",
            "database_sha256": "c" * 64,
            "endpoint_sha256": source_endpoint,
        },
        "facts": selected,
        "facts_sha256": evidence._sha256_json(selected),
    }
    evidence._write_json(path, payload)
    return evidence._sha256_bytes(path.read_bytes())


def _verify_args(
    tmp_path: Path,
    snapshot: Path,
    snapshot_sha: str,
    *,
    staff_username: str = STAFF_USERNAME,
) -> argparse.Namespace:
    return argparse.Namespace(
        release_sha=SHA,
        snapshot=str(snapshot),
        expected_snapshot_sha256=snapshot_sha,
        staff_username=staff_username,
        restored_storage_dir=str(tmp_path / "restored" / "storage"),
        output=str(tmp_path / "RESTORE_EVIDENCE.json"),
    )


def test_release_sha_requires_exact_commit_sha() -> None:
    assert evidence._release_sha("A" * 40) == SHA
    for invalid in ("", "a" * 39, "a" * 41, "not-a-sha"):
        with pytest.raises(evidence.RestoreEvidenceError, match="exact 40-character"):
            evidence._release_sha(invalid)


def test_snapshot_requires_external_file_hash_and_internal_facts_hash(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot_sha = _write_snapshot(snapshot)

    loaded = evidence._read_snapshot(snapshot, expected_sha256=snapshot_sha)
    assert loaded["facts_sha256"] == evidence._sha256_json(loaded["facts"])

    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    payload["facts"]["case"]["status"] = "TAMPERED"
    snapshot.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(evidence.RestoreEvidenceError, match="file SHA-256"):
        evidence._read_snapshot(snapshot, expected_sha256=snapshot_sha)

    tampered_sha = evidence._sha256_bytes(snapshot.read_bytes())
    with pytest.raises(evidence.RestoreEvidenceError, match="facts checksum"):
        evidence._read_snapshot(snapshot, expected_sha256=tampered_sha)


def test_evidence_output_is_write_once(tmp_path: Path) -> None:
    output = tmp_path / "evidence.json"
    evidence._write_json(output, {"status": "FIRST"})

    with pytest.raises(evidence.RestoreEvidenceError, match="already exists"):
        evidence._write_json(output, {"status": "SECOND"})


def test_storage_path_rejects_traversal_and_symlink_components(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    document = root / "cases" / "11" / "document.dlcenc"
    document.parent.mkdir(parents=True)
    document.write_bytes(b"ciphertext")

    relative, selected = evidence._source_storage_location(
        str(document),
        storage_root=root,
    )
    assert relative == "cases/11/document.dlcenc"
    assert selected == document.resolve()

    with pytest.raises(evidence.RestoreEvidenceError, match="safe relative path"):
        evidence._restored_storage_path(
            "../outside.dlcenc",
            restored_storage_root=root,
        )

    alias = root / "alias"
    alias.symlink_to(root / "cases", target_is_directory=True)
    with pytest.raises(evidence.RestoreEvidenceError, match="Symbolic links"):
        evidence._restored_storage_path(
            "alias/11/document.dlcenc",
            restored_storage_root=root,
        )


def test_privacy_minimized_facts_do_not_emit_direct_identifiers() -> None:
    case = SimpleNamespace(
        id=1,
        case_number="M2-SECRET-CASE",
        route="M2",
        status="M2_CONSULTATION_BOOKED",
        assigned_lawyer_id=None,
        close_reason=None,
        closed_at=None,
        archived_at=None,
        content_deleted_at=None,
    )
    payment = SimpleNamespace(
        id=2,
        case_id=1,
        payment_code="PAY-SECRET",
        amount="5000.00",
        currency="RUB",
        status="PAID",
        provider="yookassa",
        provider_payment_id="provider-secret-id",
        reservation_key="reservation-secret",
        paid_at=None,
        failed_at=None,
        cancelled_at=None,
        refunded_at=None,
        expired_at=None,
    )
    staff = SimpleNamespace(
        id=3,
        username="person@example.test",
        role="admin",
        is_active=True,
        mfa_enabled=True,
        session_version=4,
    )

    case_fact = evidence._case_fact(case)
    payment_fact = evidence._payment_fact(payment)
    staff_fact = evidence._staff_fact(staff)

    serialized = json.dumps(
        {"case": case_fact, "payment": payment_fact, "staff": staff_fact},
        sort_keys=True,
    )
    assert "M2-SECRET-CASE" not in serialized
    assert "PAY-SECRET" not in serialized
    assert "provider-secret-id" not in serialized
    assert "reservation-secret" not in serialized
    assert "person@example.test" not in serialized
    assert "case_number_sha256" in case_fact
    assert "payment_code_sha256" in payment_fact
    assert "username_sha256" in staff_fact


def test_verify_rejects_source_database_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot_sha = _write_snapshot(snapshot, source_endpoint="same-endpoint")
    monkeypatch.setattr(
        evidence,
        "_database_identity",
        lambda: {
            "backend": "postgresql",
            "database": "legal_drill",
            "database_sha256": "d" * 64,
            "endpoint_sha256": "same-endpoint",
        },
    )

    with pytest.raises(evidence.RestoreEvidenceError, match="source database endpoint"):
        asyncio.run(evidence.verify(_verify_args(tmp_path, snapshot, snapshot_sha)))


def test_verify_rejects_target_without_restore_suffix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot_sha = _write_snapshot(snapshot)
    monkeypatch.setattr(
        evidence,
        "_database_identity",
        lambda: {
            "backend": "postgresql",
            "database": "legal_production",
            "database_sha256": "d" * 64,
            "endpoint_sha256": "different-endpoint",
        },
    )

    with pytest.raises(evidence.RestoreEvidenceError, match="must end with"):
        asyncio.run(evidence.verify(_verify_args(tmp_path, snapshot, snapshot_sha)))


def test_verify_rejects_wrong_staff_identity_before_collecting_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot_sha = _write_snapshot(snapshot)
    monkeypatch.setattr(
        evidence,
        "_database_identity",
        lambda: {
            "backend": "postgresql",
            "database": "legal_restore",
            "database_sha256": "d" * 64,
            "endpoint_sha256": "different-endpoint",
        },
    )

    with pytest.raises(evidence.RestoreEvidenceError, match="Staff identity"):
        asyncio.run(
            evidence.verify(
                _verify_args(
                    tmp_path,
                    snapshot,
                    snapshot_sha,
                    staff_username="different-user",
                )
            )
        )


def test_verify_emits_restore_pass_only_for_exact_restored_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = _facts()
    snapshot = tmp_path / "snapshot.json"
    snapshot_sha = _write_snapshot(snapshot, facts=facts)
    monkeypatch.setattr(
        evidence,
        "_database_identity",
        lambda: {
            "backend": "postgresql",
            "database": "legal_drill",
            "database_sha256": "d" * 64,
            "endpoint_sha256": "different-endpoint",
        },
    )

    captured: dict[str, object] = {}

    async def fake_collect_facts(**kwargs):  # noqa: ANN003, ANN202
        captured.update(kwargs)
        return facts

    monkeypatch.setattr(evidence, "_collect_facts", fake_collect_facts)
    args = _verify_args(tmp_path, snapshot, snapshot_sha)
    result = asyncio.run(evidence.verify(args))

    assert result["status"] == "RESTORE_PASS"
    assert result["release_sha"] == SHA
    assert result["checks"]["audit_chain_valid"] is True
    assert result["checks"]["historical_document_decryption"] is True
    assert result["counts"] == {
        "audit_chain_events": 7,
        "payment_events": 1,
        "case_audit_events": 1,
    }
    assert captured["expected_storage_relative_path"] == "cases/11/document.dlcenc"
    assert captured["staff_username"] == STAFF_USERNAME
    assert "database" not in result["target_database"]
    persisted = json.loads(Path(args.output).read_text(encoding="utf-8"))
    assert persisted == result


def test_verify_rejects_any_restored_fact_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = _facts()
    snapshot = tmp_path / "snapshot.json"
    snapshot_sha = _write_snapshot(snapshot, facts=facts)
    monkeypatch.setattr(
        evidence,
        "_database_identity",
        lambda: {
            "backend": "postgresql",
            "database": "legal_test",
            "database_sha256": "d" * 64,
            "endpoint_sha256": "different-endpoint",
        },
    )

    async def changed_facts(**_kwargs):  # noqa: ANN003, ANN202
        drifted = json.loads(json.dumps(facts))
        drifted["payment"]["status"] = "REFUNDED"
        return drifted

    monkeypatch.setattr(evidence, "_collect_facts", changed_facts)

    with pytest.raises(evidence.RestoreEvidenceError, match="do not exactly match"):
        asyncio.run(evidence.verify(_verify_args(tmp_path, snapshot, snapshot_sha)))
