"""Staff inbox and read receipts must not expose or mutate erased/closed cases."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import message_center
from app.domain.messages.message_service import MessageService
from app.models import Base
from app.models.case import Case
from app.models.case_retention import CaseRetentionRecord
from app.models.message import Message
from app.models.user import User


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'readonly_inbox.db'}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _staff_scope(*_args, **_kwargs):
    return SimpleNamespace(
        lawyer_id=None,
        allows_case=lambda _case: True,
    )


async def _seed(db, *, status="M1_CLOSED"):
    user = User(telegram_id=908202601)
    db.add(user)
    await db.flush()
    case = Case(
        case_number="READONLY-INBOX-001",
        client_id=user.id, status=status, route="M1",
    )
    db.add(case)
    await db.flush()
    msg = Message(
        case_id=case.id, sender_id=user.id, sender_type="client",
        text="Synthetic private data 123456789", is_read=False,
    )
    db.add(msg)
    await db.flush()
    return user, case, msg


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "retention",
    ["ERASED", "APPROVED", "EXECUTING", "FAILED", "COMPLETED"],
)
async def test_inbox_and_thread_hide_private_text_after_erasure_gate(
    factory, monkeypatch, retention,
):
    monkeypatch.setattr(message_center, "require_staff_scope", _staff_scope)
    async with factory() as db:
        _user, case, message = await _seed(db)
        case_id, message_id = case.id, message.id
        if retention == "ERASED":
            case.content_deleted_at = datetime.now(timezone.utc)
        else:
            db.add(CaseRetentionRecord(
                case_id=case.id,
                status=retention,
                retention_due_at=datetime.now(timezone.utc),
            ))
        await db.commit()

        inbox = await message_center.message_center_status(
            request=object(), db=db, x_admin_token=None,
        )
        assert inbox["conversation_count"] == 0
        assert inbox["items"] == []
        assert "Synthetic private data" not in repr(inbox)

        with pytest.raises(HTTPException) as denied:
            await message_center.case_messages(
                case_id=case_id, request=object(), db=db,
                x_admin_token=None,
            )
        assert denied.value.status_code == 410

        with pytest.raises(HTTPException) as denied_read:
            await message_center.mark_message_read(
                message_id=message_id, request=object(), db=db,
                x_admin_token=None,
            )
        assert denied_read.value.status_code == 410
        await db.refresh(message)
        assert message.is_read is False


@pytest.mark.asyncio
async def test_closed_thread_remains_readable_but_does_not_mark_read(factory, monkeypatch):
    monkeypatch.setattr(message_center, "require_staff_scope", _staff_scope)
    async with factory() as db:
        _, case, message = await _seed(db)
        case_id, message_id = case.id, message.id
        await db.commit()

        thread = await message_center.case_messages(
            case_id=case_id, request=object(), db=db, x_admin_token=None,
        )
        assert thread["case"]["id"] == case_id
        assert thread["messages"][0]["text"] == message.text

        await db.refresh(message)
        assert message.is_read is False
        with pytest.raises(HTTPException) as denied:
            await message_center.mark_message_read(
                message_id=message_id, request=object(), db=db,
                x_admin_token=None,
            )
        assert denied.value.status_code == 409
        await db.refresh(message)
        assert message.is_read is False

        # Both service-level read-marking paths also respect archive immutability.
        svc = MessageService(db)
        assert await svc.mark_client_messages_read(case_id) == 0
        assert await svc.mark_lawyer_messages_read(case_id) == 0
        await db.refresh(message)
        assert message.is_read is False


@pytest.mark.asyncio
async def test_active_thread_can_still_mark_client_message_read(factory, monkeypatch):
    monkeypatch.setattr(message_center, "require_staff_scope", _staff_scope)
    async with factory() as db:
        _, case, message = await _seed(db, status="M1_DOCUMENTS_PENDING")
        await db.commit()

        thread = await message_center.case_messages(
            case_id=case.id, request=object(), db=db, x_admin_token=None,
        )
        assert thread["case"]["id"] == case.id
        await db.refresh(message)
        assert message.is_read is True
