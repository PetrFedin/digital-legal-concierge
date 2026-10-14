"""Disposable PostgreSQL proof that historical case audit cannot escape approval.

The CI PostgreSQL service is migrated and isolated; never point this test at a
customer database. No sealed audit payloads are rewritten or deleted here.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import audit_center
from app.domain.cases.case_activity import CaseActivityService, CaseActivityUnavailable
from app.domain.retention.content_visibility import restricted_case_ids
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.case_retention import CaseRetentionRecord
from app.models.user import User
from app.security.audit_integrity import verify_audit_chain


pytestmark = pytest.mark.asyncio


async def test_postgresql_approved_retention_hides_sealed_audit_before_deletion(monkeypatch):
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.skip("Disposable migrated PostgreSQL runner required")
    monkeypatch.setattr(
        audit_center, "require_audit_superadmin",
        lambda token: {"roles": ["superadmin"]},
    )
    engine = create_async_engine(url, pool_size=3, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    token = uuid4().hex
    unique_id = int(token[:10], 16)
    try:
        async with sessions() as db:
            owner = User(telegram_id=2200000000000000 + unique_id)
            db.add(owner)
            await db.flush()
            visible = Case(
                case_number="PG-AUDIT-VISIBLE-" + token[:18],
                client_id=owner.id, status="M1_CLOSED", route="M1",
            )
            erasing = Case(
                case_number="PG-AUDIT-ERASING-" + token[:18],
                client_id=owner.id, status="M1_CLOSED", route="M1",
            )
            db.add_all((visible, erasing))
            await db.flush()
            visible_id, erasing_id = visible.id, erasing.id
            visible_audit = AuditLog(
                actor_type="lawyer", actor_id=55,
                action="DOCUMENT_REVIEW_DECISION",
                entity_type="case", entity_id=visible_id,
                new_value={"status": "NEEDS_REUPLOAD"},
                comment="VISIBLE-SYNTHETIC-COMMENT",
            )
            erased_audit = AuditLog(
                actor_type="lawyer", actor_id=56,
                action="DOCUMENT_REVIEW_DECISION",
                entity_type="case", entity_id=erasing_id,
                new_value={"status": "NEEDS_REUPLOAD"},
                comment="REDACT-ME-SYNTHETIC-PRIVATE",
            )
            db.add_all((visible_audit, erased_audit))
            await db.commit()
            source_ids = visible_audit.id, erased_audit.id
            source_hash = erased_audit.event_hash

        # A normal closed Case remains legitimately readable.
        async with sessions() as db:
            activity = await CaseActivityService(db).page(
                case_id=visible_id, audience="staff",
            )
            assert "VISIBLE-SYNTHETIC" in repr(activity)

        # Separate transaction commits deletion approval while actual content,
        # sealed audit and the case record still physically exist.
        async with sessions() as db:
            db.add(CaseRetentionRecord(
                case_id=erasing_id,
                status="APPROVED",
                retention_due_at=datetime.now(timezone.utc),
            ))
            await db.commit()

        async with sessions() as db:
            assert await restricted_case_ids(db, case_ids={visible_id, erasing_id}) == {erasing_id}
            with pytest.raises(CaseActivityUnavailable):
                await CaseActivityService(db).page(
                    case_id=erasing_id, audience="staff",
                )
            payload = await audit_center.audit_center_status(
                limit=100, db=db, x_admin_token="synthetic",
            )
            items = {row["id"]: row for row in payload["items"]}
            # The two newly appended audits must be in the bounded newest page.
            assert all(key in items for key in source_ids)
            permitted = items[source_ids[0]]
            protected = items[source_ids[1]]
            assert permitted["comment"] == "VISIBLE-SYNTHETIC-COMMENT"
            assert permitted["entity_id"] == visible_id
            assert protected["comment"] is None
            assert protected["comment_redacted"] is True
            assert protected["actor_id"] is None
            assert protected["entity_id"] is None
            assert "REDACT-ME-SYNTHETIC-PRIVATE" not in repr(payload)

            sealed = await db.get(AuditLog, source_ids[1])
            assert sealed.comment == "REDACT-ME-SYNTHETIC-PRIVATE"
            assert sealed.event_hash == source_hash
            check = await verify_audit_chain(db)
            assert check["ok"] is True
    finally:
        await engine.dispose()
