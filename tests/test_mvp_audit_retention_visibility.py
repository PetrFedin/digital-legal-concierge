"""Read-side privacy gate for sealed audit: do not rewrite legacy evidence.

SQLite tests verify actual API/service projections; the PostgreSQL runner separately
exercises retention blocking and multi-case isolation. Approval hides legacy text
immediately, before the destructive retention worker has removed content.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import audit_center, case_timeline
from app.config import settings
from app.domain.cases.case_activity import (
    CaseActivityService,
    CaseActivityUnavailable,
)
from app.domain.retention.content_visibility import restricted_case_ids
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.case_retention import CaseRetentionRecord
from app.models.user import User
from app.security.audit_integrity import verify_audit_chain


async def allow_synthetic_admin(db, token):
    return {"roles": ["superadmin"]}


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "audit_integrity_key_id", "audit-visibility-test")
    monkeypatch.setattr(
        settings, "audit_integrity_key",
        "audit-visibility-test-" + "s" * 48,
    )
    monkeypatch.setattr(settings, "audit_integrity_previous_keys", "")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'audit-visibility.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def seed_case(db, *, case_status="M1_CLOSED", erased=False, retention=None):
    unique = uuid4().hex
    user = User(telegram_id=40000000000000 + int(unique[:10], 16))
    db.add(user)
    await db.flush()
    case = Case(
        client_id=user.id, route="M1",
        case_number="SEALED-" + unique[:24], status=case_status,
    )
    if erased:
        case.content_deleted_at = datetime.now(timezone.utc)
    db.add(case)
    await db.flush()
    if retention is not None:
        db.add(CaseRetentionRecord(
            case_id=case.id, status=retention,
            retention_due_at=datetime.now(timezone.utc),
        ))
    # An intentionally sensitive legacy event. New business code must not
    # duplicate message bodies, but old rows remain tamper-evident and intact.
    audit = AuditLog(
        actor_type="lawyer", actor_id=127,
        action="DOCUMENT_REVIEW_DECISION",
        entity_type="case", entity_id=case.id,
        old_value={"text": "OLD-PRIVATE-NUMBER-00001"},
        new_value={"status": "NEEDS_REUPLOAD", "document_id": 3},
        comment="SEALED-SENSITIVE-CLIENT-NAME-XYZ",
    )
    db.add(audit)
    await db.commit()
    return case.id, audit.id, audit.event_hash


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gate", ("ERASED", "APPROVED", "EXECUTING", "FAILED", "COMPLETED")
)
async def test_staff_timeline_and_activity_refuse_legacy_content(store, monkeypatch, gate):
    monkeypatch.setattr(case_timeline, "require_admin", lambda token: None)
    async with store() as db:
        case_id, audit_id, original_hash = await seed_case(
            db, erased=gate == "ERASED",
            retention=None if gate == "ERASED" else gate,
        )

        with pytest.raises(HTTPException) as denied:
            await case_timeline.workdesk_case_timeline(
                case_id=case_id, db=db, x_admin_token="synthetic",
            )
        assert denied.value.status_code == 410
        assert "SEALED-SENSITIVE" not in denied.value.detail

        with pytest.raises(CaseActivityUnavailable):
            await CaseActivityService(db).page(case_id=case_id, audience="staff")

        stored = await db.get(AuditLog, audit_id)
        assert stored.comment == "SEALED-SENSITIVE-CLIENT-NAME-XYZ"
        assert stored.old_value["text"] == "OLD-PRIVATE-NUMBER-00001"
        assert stored.event_hash == original_hash
        assert (await verify_audit_chain(db))["ok"] is True


@pytest.mark.asyncio
async def test_closed_case_without_approved_deletion_keeps_readable_history(
    store, monkeypatch,
):
    monkeypatch.setattr(case_timeline, "require_admin", lambda token: None)
    async with store() as db:
        case_id, _, _ = await seed_case(db, retention="REQUESTED")
        result = await case_timeline.workdesk_case_timeline(
            case_id=case_id, db=db, x_admin_token="synthetic",
        )
        assert result["case_id"] == case_id
        assert result["count"] == 1
        assert "SEALED-SENSITIVE-CLIENT-NAME-XYZ" in result["items"][0]["detail"]


@pytest.mark.asyncio
async def test_staff_timeline_missing_case_returns_not_found(store, monkeypatch):
    monkeypatch.setattr(case_timeline, "require_admin", lambda token: None)
    async with store() as db:
        with pytest.raises(HTTPException) as denied:
            await case_timeline.workdesk_case_timeline(
                case_id=10**9, db=db, x_admin_token="synthetic",
            )
        assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_audit_center_masks_sealed_comments_but_preserves_chain(store, monkeypatch):
    monkeypatch.setattr(
        audit_center, "require_audit_superadmin", allow_synthetic_admin,
    )
    async with store() as db:
        hidden_case, audit_id, original_hash = await seed_case(
            db, retention="APPROVED"
        )
        response = await audit_center.audit_center_status(
            db=db, x_admin_token="synthetic",
        )
        assert response["count"] == 1
        assert response["integrity"]["ok"] is True
        projected = response["items"][0]
        assert projected["chain_sequence"] == 1
        assert projected["event_hash_prefix"] == original_hash[:12]
        assert projected["actor_id"] is None
        assert projected["entity_id"] is None
        assert projected["comment"] is None
        assert projected["comment_redacted"] is True
        # Retained internal audit IDs must not be mistaken for content disclosure.
        assert projected["entity_id"] is None
        assert "SEALED-SENSITIVE" not in repr(response)
        assert "OLD-PRIVATE-NUMBER" not in repr(response)

        source = await db.get(AuditLog, audit_id)
        assert source.comment == "SEALED-SENSITIVE-CLIENT-NAME-XYZ"
        assert source.event_hash == original_hash
        assert (await verify_audit_chain(db))["ok"] is True


@pytest.mark.asyncio
async def test_orphan_case_and_noncase_audit_comments_are_not_exposed(store, monkeypatch):
    monkeypatch.setattr(
        audit_center, "require_audit_superadmin", allow_synthetic_admin,
    )
    async with store() as db:
        db.add_all((
            AuditLog(
                actor_type="lawyer", actor_id=1, action="DOCUMENT_REVIEW_DECISION",
                entity_type="case", entity_id=99999999,
                comment="ORPHAN-PRIVATE-CLIENT-TEXT",
            ),
            AuditLog(
                actor_type="system", actor_id=2, action="INTERNAL_DIAGNOSTIC",
                entity_type="other", entity_id=10,
                comment="OTHER-ENTITY-PRIVATE-CLIENT-TEXT",
            ),
        ))
        await db.commit()
        result = await audit_center.audit_center_status(
            db=db, x_admin_token="synthetic",
        )
        assert result["integrity"]["ok"] is True
        assert len(result["items"]) == 2
        assert all(item["comment"] is None for item in result["items"])
        assert all(item["comment_redacted"] is True for item in result["items"])
        assert "PRIVATE-CLIENT" not in repr(result)


@pytest.mark.asyncio
async def test_audit_center_keeps_active_case_comment_for_authorized_staff(
    store, monkeypatch,
):
    monkeypatch.setattr(
        audit_center, "require_audit_superadmin", allow_synthetic_admin,
    )
    async with store() as db:
        case_id, _, _ = await seed_case(db, case_status="M1_DOCUMENTS_PENDING")
        result = await audit_center.audit_center_status(
            db=db, x_admin_token="synthetic",
        )
        assert result["integrity"]["ok"] is True
        assert result["items"][0]["entity_id"] == case_id
        assert result["items"][0]["comment"] == "SEALED-SENSITIVE-CLIENT-NAME-XYZ"
        assert result["items"][0]["comment_redacted"] is False


@pytest.mark.asyncio
async def test_batch_restricted_case_lookup_is_fail_closed_for_missing_ids(store):
    async with store() as db:
        case_id, _, _ = await seed_case(db, retention="REQUESTED")
        missing_id = 90909090
        first = await restricted_case_ids(
            db, case_ids={case_id, missing_id}
        )
        assert first == {missing_id}
        record = (
            await db.scalars(
                select(CaseRetentionRecord).where(
                    CaseRetentionRecord.case_id == case_id
                )
            )
        ).one()
        record.status = "EXECUTING"
        await db.flush()
        second = await restricted_case_ids(
            db, case_ids={case_id, missing_id}
        )
        assert second == {case_id, missing_id}
