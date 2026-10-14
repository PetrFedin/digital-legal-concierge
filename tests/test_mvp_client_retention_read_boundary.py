"""Regression: deletion approval immediately blocks sensitive client reads.

Tests the actual SQL services used by Telegram rather than only mocking the
screen handler. Approval/worker delay does not authorize old callback, message
preview, unread count, payment link, or completed consultation disclosure.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot import consultation_result
from app.bot.screens import archived_cases, payments
from app.domain.cases.case_service import CaseService
from app.domain.messages.message_service import MessageService
from app.domain.retention.content_visibility import private_case_content_unavailable
from app.models import Base
from app.models.case import Case
from app.models.case_retention import CaseRetentionRecord
from app.models.consultation import Consultation
from app.models.message import Message
from app.models.payment import Payment
from app.models.user import User


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'client-read-fence.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def seed_user_with_cases(db, *, blocked_status="M1_CLOSED", retention="APPROVED"):
    suffix = uuid4().hex[:12]
    user = User(telegram_id=500000000000 + int(suffix[:9], 16))
    other = User(telegram_id=600000000000 + int(suffix[:9], 16))
    db.add_all((user, other))
    await db.flush()
    active_safe = Case(
        client_id=user.id, status="M1_DOCUMENTS_PENDING", route="M1",
        case_number="SAFE-ACT-" + suffix,
    )
    active_blocked = Case(
        client_id=user.id, status="M1_DOCUMENTS_PENDING", route="M1",
        case_number="BLOCK-ACT-" + suffix,
    )
    closed_safe = Case(
        client_id=user.id, status="M1_CLOSED", route="M1",
        case_number="SAFE-CLOSED-" + suffix,
    )
    closed_blocked = Case(
        client_id=user.id, status=blocked_status, route="M1",
        case_number="BLOCK-CLOSED-" + suffix,
    )
    foreign = Case(
        client_id=other.id, status="M1_CLOSED", route="M1",
        case_number="FOREIGN-CLOSED-" + suffix,
    )
    db.add_all((active_safe, active_blocked, closed_safe, closed_blocked, foreign))
    await db.flush()
    user.active_case_id = active_blocked.id

    db.add_all((
        CaseRetentionRecord(
            case_id=active_blocked.id, status=retention,
            retention_due_at=datetime.now(timezone.utc),
        ),
        CaseRetentionRecord(
            case_id=closed_blocked.id, status=retention,
            retention_due_at=datetime.now(timezone.utc),
        ),
    ))
    safe_message = Message(
        case_id=closed_safe.id, sender_type="lawyer", text="VISIBLE-TEST-SAFE",
        sender_id=user.id, is_read=False,
    )
    hidden_message = Message(
        case_id=closed_blocked.id, sender_type="lawyer",
        text="RESTRICTED-PRIVATE-LAWYER-TEXT", sender_id=user.id, is_read=False,
    )
    db.add_all((safe_message, hidden_message))
    db.add_all((
        Consultation(
            case_id=closed_safe.id, status="DONE",
            client_description="safe synthetic",
            lawyer_result="VISIBLE-CONSULTATION",
            decision="close",
        ),
        Consultation(
            case_id=closed_blocked.id, status="DONE",
            client_description="RESTRICTED-CLIENT-STORY",
            lawyer_result="RESTRICTED-LAWYER-OUTCOME",
            decision="close",
        ),
    ))
    payment = Payment(
        case_id=closed_blocked.id, payment_code="M1_INITIAL_PAYMENT",
        title="RESTRICTED-30K-PAYMENT", amount=Decimal("30000"),
        status="PENDING", provider="fake", payment_url="https://example.invalid/pay",
    )
    db.add(payment)
    await db.commit()
    return {
        "user_id": user.id,
        "other_id": other.id,
        "safe_active": active_safe.id,
        "blocked_active": active_blocked.id,
        "safe_closed": closed_safe.id,
        "blocked_closed": closed_blocked.id,
        "foreign_closed": foreign.id,
        "hidden_message_id": hidden_message.id,
        "safe_message_id": safe_message.id,
        "payment_id": payment.id,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("retention", ("APPROVED", "EXECUTING", "FAILED", "COMPLETED"))
async def test_current_client_queries_exclude_approved_before_physical_deletion(
    factory, monkeypatch, retention,
):
    async with factory() as db:
        refs = await seed_user_with_cases(db, retention=retention)
        user_id = refs["user_id"]
        service = CaseService(db)
        selected = await service.get_active_case_for_user(user_id)
        assert selected is not None and selected.id == refs["safe_active"]
        latest = await service.get_latest_case_for_user(user_id)
        assert latest is not None and latest.id != refs["blocked_closed"]
        assert await service.get_terminal_case_for_user(
            user_id=user_id, case_id=refs["blocked_closed"],
        ) is None
        assert await service.get_terminal_case_for_user(
            user_id=user_id, case_id=refs["foreign_closed"],
        ) is None
        allowed = await service.get_terminal_case_for_user(
            user_id=user_id, case_id=refs["safe_closed"],
        )
        assert allowed is not None and allowed.id == refs["safe_closed"]
        actor = await db.get(User, user_id)
        assert await service.select_active_case(
            user=actor, case_id=refs["blocked_active"],
        ) is None
        assert actor.active_case_id == refs["blocked_active"]
        picked = await service.select_active_case(
            user=actor, case_id=refs["safe_active"],
        )
        assert picked is not None and picked.id == refs["safe_active"]

        message_service = MessageService(db)
        assert await message_service.list_case_messages(refs["blocked_closed"]) == []
        blocked_rows, page, pages = await message_service.list_case_messages_page(
            refs["blocked_closed"], page=0, page_size=5,
        )
        assert (blocked_rows, page, pages) == ([], 0, 1)
        assert await message_service.latest_message_id(refs["blocked_closed"]) is None
        unread, latest_at = await message_service.unread_lawyer_summary(
            refs["blocked_closed"]
        )
        assert unread == 0 and latest_at is None
        safe_rows = await message_service.list_case_messages(refs["safe_closed"])
        assert len(safe_rows) == 1 and safe_rows[0].text == "VISIBLE-TEST-SAFE"

        consultation = await consultation_result.latest_terminal_client_consultation(
            db, client_id=user_id,
        )
        assert consultation is not None
        assert consultation[0].id == refs["safe_closed"]
        assert consultation[1].lawyer_result == "VISIBLE-CONSULTATION"
        assert await consultation_result.latest_terminal_client_consultation(
            db, client_id=user_id, case_id=refs["blocked_closed"],
        ) is None

        class StubContext:
            def __init__(self, db):
                self.case_service = CaseService(db)
            async def get_user_from_callback(self, callback):
                return SimpleNamespace(id=user_id)
        monkeypatch.setattr(payments, "BotContextService", StubContext)

        class Callback:
            def __init__(self):
                self.alerts = []
            async def answer(self, message, **kwargs):
                self.alerts.append((message, kwargs))
        callback = Callback()
        payable, case = await payments.get_owned_payment(
            callback, db, refs["payment_id"]
        )
        assert payable is case is None
        assert callback.alerts and callback.alerts[-1][1]["show_alert"] is True

        # The approved deletion has not physically erased the source rows.
        retained = await db.get(Message, refs["hidden_message_id"])
        assert retained.text == "RESTRICTED-PRIVATE-LAWYER-TEXT"
        blocked = await db.get(Case, refs["blocked_closed"])
        assert blocked.content_deleted_at is None
        assert await private_case_content_unavailable(db, case=blocked)


@pytest.mark.asyncio
async def test_requested_retention_does_not_hide_legitimate_closed_history(factory):
    async with factory() as db:
        refs = await seed_user_with_cases(db, retention="REQUESTED")
        service = CaseService(db)
        case = await service.get_terminal_case_for_user(
            user_id=refs["user_id"], case_id=refs["blocked_closed"],
        )
        assert case is not None
        assert await private_case_content_unavailable(db, case=case) is False
        messages = await MessageService(db).list_case_messages(
            refs["blocked_closed"]
        )
        assert len(messages) == 1
        assert messages[0].text == "RESTRICTED-PRIVATE-LAWYER-TEXT"
        result = await consultation_result.latest_terminal_client_consultation(
            db, client_id=refs["user_id"], case_id=refs["blocked_closed"],
        )
        assert result is not None


@pytest.mark.asyncio
async def test_erased_case_with_no_retention_row_is_still_excluded(factory):
    async with factory() as db:
        refs = await seed_user_with_cases(db, retention="REQUESTED")
        erased = await db.get(Case, refs["blocked_closed"])
        erased.content_deleted_at = datetime.now(timezone.utc)
        await db.commit()
        assert await CaseService(db).get_latest_case_for_user(
            refs["user_id"]
        ) is not None
        assert await MessageService(db).list_case_messages(
            refs["blocked_closed"]
        ) == []
        assert await consultation_result.latest_terminal_client_consultation(
            db, client_id=refs["user_id"], case_id=refs["blocked_closed"],
        ) is None
        # Terminal lookup may still return a tombstone with no private content.
        tombstone = await CaseService(db).get_terminal_case_for_user(
            user_id=refs["user_id"], case_id=refs["blocked_closed"],
        )
        assert tombstone is not None and tombstone.content_deleted_at is not None


@pytest.mark.asyncio
async def test_old_archive_callback_denied_before_domain_reads_after_approval(
    factory, monkeypatch,
):
    async with factory() as db:
        refs = await seed_user_with_cases(db, retention="APPROVED")
        user_id = refs["user_id"]

        class StubContext:
            def __init__(self, db):
                self.case_service = CaseService(db)
            async def get_user_from_callback(self, callback):
                return SimpleNamespace(id=user_id)
        monkeypatch.setattr(archived_cases, "BotContextService", StubContext)

        async def no_private_view(*args, **kwargs):
            raise AssertionError("Approved deletion must not load private history")
        monkeypatch.setattr(
            archived_cases, "load_client_case_view", no_private_view,
        )

        class NoPrivateService:
            def __init__(self, db):
                raise AssertionError("Archive cannot open private content after approval")
        for name in ("DocumentService", "PaymentService", "CaseActivityService", "MessageService"):
            monkeypatch.setattr(archived_cases, name, NoPrivateService)

        class FakeMessage:
            def __init__(self):
                self.edits = []
            async def edit_text(self, text, reply_markup=None):
                self.edits.append((text, reply_markup))

        class FakeCallback:
            def __init__(self, data):
                self.data = data
                self.message = FakeMessage()
                self.alerts = []
            async def answer(self, text=None, **kwargs):
                self.alerts.append((text, kwargs))

        for action in (
            f"cases_archive:{refs['blocked_closed']}",
            f"archive_docs:{refs['blocked_closed']}:0",
            f"archive_payments:{refs['blocked_closed']}:0",
            f"archive_history:{refs['blocked_closed']}:0",
            f"archive_messages:{refs['blocked_closed']}:0",
        ):
            callback = FakeCallback(action)
            await archived_cases.open_readonly_archive(callback, db)
            assert callback.message.edits
            text, buttons = callback.message.edits[-1]
            assert "недоступно" in text
            assert "RESTRICTED" not in text
            assert [
                button.callback_data
                for row in buttons.inline_keyboard
                for button in row
            ] == ["cases_list", "nav_home"]
