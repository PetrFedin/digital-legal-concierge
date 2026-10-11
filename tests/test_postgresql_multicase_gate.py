"""PostgreSQL-only admission for multicasing, concurrent save, and tenant isolation.

Runs against the disposable PostgreSQL service in GitHub Actions, never production.
"""
from __future__ import annotations

import asyncio
import os
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cases.case_service import CaseService
from app.domain.cases.new_object_service import NewObjectSaveService
from app.domain.documents.document_service import DocumentService
from app.domain.messages.message_service import MessageService
from app.domain.payments.payment_service import PaymentService
from app.models.calculation import Calculation
from app.models.case import Case
from app.models.document import Document
from app.models.message import Message
from app.models.payment import Payment
from app.models.user import User


pytestmark = pytest.mark.asyncio


async def test_postgresql_concurrent_new_object_save_scoped_visibility():
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.skip("Disposable real PostgreSQL service required")
    engine = create_async_engine(url, pool_size=5, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    token = uuid4().hex
    try:
        async with factory() as db:
            owner = User(telegram_id=800000000000000 + int(token[:10], 16))
            stranger = User(telegram_id=900000000000000 + int(token[10:20], 16))
            db.add_all([owner, stranger])
            await db.flush()
            original = await CaseService(db).create_case(
                client=owner, title="Original independent matter",
            )
            original_id, owner_id, stranger_id = original.id, owner.id, stranger.id
            original.status = "M1_DOCUMENTS_PENDING"
            original.route = "M1"
            db.add_all([
                Document(case_id=original_id, document_type="DDU", title="DDU source",
                         file_name="document.pdf", file_path="/synthetic/original.pdf",
                         status="UPLOADED"),
                Message(case_id=original_id, sender_type="client",
                        sender_id=owner_id, text="Original private message"),
                Payment(case_id=original_id, payment_code="M1_INITIAL_PAYMENT",
                        title="Original payment", amount=Decimal("30000"),
                        status="PENDING"),
            ])
            await db.commit()

        async def save_once():
            async with factory() as db:
                user = await db.get(User, owner_id)
                created = await NewObjectSaveService(db).save(
                    user=user, preview_case_id=original_id,
                    request_key=token,
                    contract_price=Decimal("2500000"),
                    planned_transfer_date=date(2025, 1, 1),
                    calculation_date=date(2025, 3, 1),
                    object_transferred=False, actual_transfer_date=None,
                )
                case_id = created.id
                await db.commit()
                return case_id

        first, second = await asyncio.wait_for(
            asyncio.gather(save_once(), save_once()), timeout=30
        )
        assert first == second

        async with factory() as db:
            owner = await db.get(User, owner_id)
            stranger = await db.get(User, stranger_id)
            cases = await CaseService(db).list_cases_for_user(owner_id)
            assert {c.id for c in cases} == {original_id, first}
            assert await db.scalar(select(func.count(Case.id)).where(
                Case.creation_request_key == f"new-object:{owner_id}:{token}",
            )) == 1
            assert await db.scalar(select(func.count(Calculation.id)).where(
                Calculation.case_id == first,
            )) == 1
            assert (await db.get(Case, original_id)).status == "M1_DOCUMENTS_PENDING"
            assert await CaseService(db).select_active_case(
                user=stranger, case_id=first,
            ) is None
            assert await CaseService(db).select_active_case(
                user=owner, case_id=first,
            ) is not None
            assert (await CaseService(db).get_active_case_for_user(owner_id)).id == first
            assert (await CaseService(db).get_active_case_for_user(stranger_id)) is None
            assert await DocumentService(db).list_case_documents(first) == []
            assert await MessageService(db).list_case_messages(first) == []
            assert await PaymentService(db).list_case_payments(first) == []
            assert len(await DocumentService(db).list_case_documents(original_id)) == 1
            assert len(await MessageService(db).list_case_messages(original_id)) == 1
            assert len(await PaymentService(db).list_case_payments(original_id)) == 1
            await db.rollback()
    finally:
        await engine.dispose()
