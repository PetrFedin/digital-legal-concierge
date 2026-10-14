"""Real, isolated PostgreSQL proof: approved retention blocks client reads.

Approval is committed in a separate session *after* client content has been
created and seen. The next READ COMMITTED statement in another session must
not return any private message, payment-facing or consultation outcome.
No destructive retention operation, provider call or production DB access.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.consultation_result import latest_terminal_client_consultation
from app.domain.cases.case_service import CaseService
from app.domain.messages.message_service import MessageService
from app.models.case import Case
from app.models.case_retention import CaseRetentionRecord
from app.models.consultation import Consultation
from app.models.message import Message
from app.models.payment import Payment
from app.models.user import User


@pytest.mark.asyncio
async def test_postgres_committed_erasure_approval_hides_client_projections():
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.skip("Only the disposable migrated PostgreSQL CI runner is supported")
    engine = create_async_engine(url, pool_size=3, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid4().hex
    try:
        async with factory() as setup:
            user = User(telegram_id=32000000000000 + int(marker[:11], 16))
            setup.add(user)
            await setup.flush()
            case = Case(
                client_id=user.id,
                case_number="PG-CONSENT-DELETION-" + marker[:20],
                route="M2",
                status="M2_CLOSED",
            )
            setup.add(case)
            await setup.flush()
            user_id, case_id = user.id, case.id
            message = Message(
                case_id=case.id,
                sender_type="lawyer",
                sender_id=user.id,
                text="SYNTHETIC-PRIVATE-CONSULTATION-TEXT",
                is_read=False,
            )
            outcome = Consultation(
                case_id=case.id,
                status="DONE",
                decision="close",
                client_description="SYNTHETIC-PRIVATE-CLIENT-STORY",
                lawyer_result="SYNTHETIC-PRIVATE-LAWYER-OUTCOME",
            )
            payment = Payment(
                case_id=case.id,
                payment_code="M2_CONSULTATION_PAYMENT",
                title="SYNTHETIC-PRIVATE-PAYMENT",
                amount=Decimal("1234.00"),
                status="PAID",
                provider="fake",
            )
            setup.add_all((message, outcome, payment))
            await setup.commit()
            message_id, payment_id = message.id, payment.id

        # A closed, non-erased Case is readable before approval.
        async with factory() as reader:
            permitted = await CaseService(reader).get_terminal_case_for_user(
                user_id=user_id, case_id=case_id
            )
            assert permitted is not None
            assert (await MessageService(reader).list_case_messages(case_id))[0].id == message_id
            visible = await latest_terminal_client_consultation(
                reader, client_id=user_id, case_id=case_id
            )
            assert visible is not None
            assert visible[1].lawyer_result == "SYNTHETIC-PRIVATE-LAWYER-OUTCOME"

            # Separate writer approves deletion and commits. The reader's next
            # READ COMMITTED statement must observe the new retention state.
            async with factory() as approver:
                approver.add(CaseRetentionRecord(
                    case_id=case_id, status="APPROVED",
                    retention_due_at=datetime.now(timezone.utc),
                ))
                await approver.commit()

            assert await CaseService(reader).get_terminal_case_for_user(
                user_id=user_id, case_id=case_id
            ) is None
            assert await CaseService(reader).get_latest_case_for_user(user_id) is None
            assert await MessageService(reader).list_case_messages(case_id) == []
            rows, page, pages = await MessageService(reader).list_case_messages_page(
                case_id, page=0, page_size=5
            )
            assert (rows, page, pages) == ([], 0, 1)
            assert await MessageService(reader).latest_message_id(case_id) is None
            assert await MessageService(reader).unread_lawyer_summary(case_id) == (0, None)
            assert await latest_terminal_client_consultation(
                reader, client_id=user_id, case_id=case_id
            ) is None

            # The privacy barrier must work *before* physical worker cleanup.
            kept_case = await reader.get(Case, case_id)
            kept_message = await reader.get(Message, message_id)
            kept_payment = await reader.get(Payment, payment_id)
            assert kept_case.content_deleted_at is None
            assert kept_message.text == "SYNTHETIC-PRIVATE-CONSULTATION-TEXT"
            assert kept_payment.title == "SYNTHETIC-PRIVATE-PAYMENT"
    finally:
        await engine.dispose()
