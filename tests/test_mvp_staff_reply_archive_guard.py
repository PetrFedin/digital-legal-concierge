"""Staff message mutation guards: terminal, erased, retention and HTTP 409."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import message_center
from app.domain.messages.message_service import (
    MessageService, StaffMessageReadOnlyError,
)
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.case_retention import CaseRetentionRecord
from app.models.message import Message
from app.models.user import User


@pytest.fixture
async def message_factory(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'staff_archive_guard.db'}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def seed_case(db):
    user = User(telegram_id=9020001)
    db.add(user)
    await db.flush()
    case = Case(
        client_id=user.id,
        case_number="SYNTH-STAFF-READONLY",
        route="M1",
        status="M1_DOCUMENTS_PENDING",
    )
    db.add(case)
    await db.flush()
    return user, case


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["M1_CLOSED", "M2_CLOSED", "ARCHIVED", "ERROR"])
async def test_staff_reply_rejected_for_closed_or_error_case(message_factory, status):
    async with message_factory() as db:
        _user, case = await seed_case(db)
        case.status = status
        await db.flush()

        with pytest.raises(StaffMessageReadOnlyError):
            await MessageService(db).create_lawyer_message(
                case=case, lawyer_id=None, text="Must not be sent",
            )
        assert (await db.scalars(select(Message))).all() == []
        assert (await db.scalars(select(AuditLog).where(
            AuditLog.action == "LAWYER_MESSAGE_CREATED",
        ))).all() == []


@pytest.mark.asyncio
async def test_staff_reply_denied_after_case_content_erasure(message_factory):
    async with message_factory() as db:
        _, case = await seed_case(db)
        case.content_deleted_at = datetime.now(timezone.utc)
        await db.flush()
        with pytest.raises(StaffMessageReadOnlyError):
            await MessageService(db).create_lawyer_message(
                case=case, lawyer_id=None, text="No new archived content",
            )
        assert (await db.scalars(select(Message))).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["APPROVED", "EXECUTING", "FAILED", "COMPLETED"])
async def test_staff_reply_denied_after_retention_approval(message_factory, status):
    async with message_factory() as db:
        _user, case = await seed_case(db)
        db.add(CaseRetentionRecord(
            case_id=case.id,
            status=status,
            retention_due_at=datetime.now(timezone.utc),
        ))
        await db.flush()
        with pytest.raises(StaffMessageReadOnlyError):
            await MessageService(db).create_lawyer_message(
                case=case, lawyer_id=None, text="No reply during deletion",
            )
        assert (await db.scalars(select(Message))).all() == []


@pytest.mark.asyncio
async def test_live_m1_staff_reply_still_produces_minimal_audit(message_factory):
    async with message_factory() as db:
        _user, case = await seed_case(db)
        reply = await MessageService(db).create_lawyer_message(
            case=case, lawyer_id=None, text="Valid message in active case",
        )
        await db.commit()
        assert reply.case_id == case.id
        audit = (await db.scalars(select(AuditLog).where(
            AuditLog.action == "LAWYER_MESSAGE_CREATED",
        ))).one()
        assert audit.new_value == {"message_id": reply.id}


@pytest.mark.asyncio
async def test_staff_reply_http_returns_conflict_without_outbox(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.rollbacks = 0
            self.commits = 0

        async def rollback(self):
            self.rollbacks += 1

        async def commit(self):
            self.commits += 1

    async def staff_scope(*args, **kwargs):
        return SimpleNamespace(
            lawyer_id=None,
            allows_case=lambda case: True,
        )

    class DenyingService:
        def __init__(self, db):
            pass

        async def lock_case(self, case_id):
            return SimpleNamespace(
                id=case_id, client_id=7, assigned_lawyer_id=None,
            )

        async def latest_message_id(self, case_id):
            return None

        async def create_lawyer_message(self, **kwargs):
            raise StaffMessageReadOnlyError()

    class MustNotNotify:
        def __init__(self, db):
            raise AssertionError("Outbox must not be created on rejected reply")

    monkeypatch.setattr(message_center, "require_staff_scope", staff_scope)
    monkeypatch.setattr(message_center, "MessageService", DenyingService)
    monkeypatch.setattr(message_center, "NotificationEngine", MustNotNotify)

    db = FakeSession()
    with pytest.raises(HTTPException) as denied:
        await message_center.reply_to_client(
            case_id=44,
            payload=message_center.ReplyPayload(
                text="Предметный ответ", expected_last_message_id=None,
            ),
            request=object(),
            db=db,
            x_admin_token=None,
        )
    assert denied.value.status_code == 409
    assert "только для чтения" in denied.value.detail
    assert db.commits == 0 and db.rollbacks == 1
