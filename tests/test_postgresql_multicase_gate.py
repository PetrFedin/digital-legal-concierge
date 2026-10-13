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
from app.domain.cases.new_object_service import NewObjectSaveError, NewObjectSaveService
from app.domain.calculator.calculator_service import CalculatorService
from app.domain.calculator.preview_receipt import preview_receipt
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
                facts = dict(
                    contract_price=Decimal("2500000"),
                    planned_transfer_date=date(2025, 1, 1),
                    calculation_date=date(2025, 3, 1),
                    object_transferred=False, actual_transfer_date=None,
                )
                receipt = preview_receipt(CalculatorService(db).calculate_preview(**facts))
                created = await NewObjectSaveService(db).save(
                    user=user, preview_case_id=original_id,
                    request_key=token, expected_receipt=receipt, **facts,
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
            # Creating a second object must not implicitly switch the current matter.
            assert (await CaseService(db).get_active_case_for_user(owner_id)).id == original_id
            assert owner.active_case_id == original_id
            facts = dict(
                contract_price=Decimal("2500000"),
                planned_transfer_date=date(2025, 1, 1),
                calculation_date=date(2025, 3, 1),
                object_transferred=False, actual_transfer_date=None,
            )
            mismatched = preview_receipt(CalculatorService(db).calculate_preview(**facts))
            mismatched["penalty_amount"] = "0"
            with pytest.raises(NewObjectSaveError, match="ключ уже использован"):
                await NewObjectSaveService(db).save(
                    user=owner, preview_case_id=original_id,
                    request_key=token, expected_receipt=mismatched, **facts,
                )
            with pytest.raises(NewObjectSaveError, match="не соответствуют показанному"):
                await NewObjectSaveService(db).save(
                    user=owner, preview_case_id=original_id,
                    request_key=token,
                    expected_receipt=preview_receipt(
                        CalculatorService(db).calculate_preview(**facts)
                    ),
                    **{**facts, "contract_price": Decimal("2600000")},
                )
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


async def test_postgresql_terminal_archive_owner_acl_and_selection_invariance():
    """Disposable PostgreSQL only: terminal lookup never crosses tenants or switches matters."""
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.skip("Disposable real PostgreSQL service required")
    engine = create_async_engine(url, pool_size=3, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    token = uuid4().hex
    try:
        async with factory() as db:
            owner = User(telegram_id=600000000000000 + int(token[:10], 16))
            stranger = User(telegram_id=700000000000000 + int(token[10:20], 16))
            db.add_all([owner, stranger])
            await db.flush()
            cases = CaseService(db)
            live = await cases.create_case(client=owner, title="Live source")
            old_m1 = await cases.create_case(
                client=owner, route="M1", status="M1_CLOSED", title="Archived M1"
            )
            old_m2 = await cases.create_case(
                client=owner, route="M2", status="M2_CLOSED", title="Archived M2"
            )
            foreign = await cases.create_case(
                client=stranger, route="M1", status="M1_CLOSED", title="Other tenant"
            )
            owned_ids = (old_m1.id, old_m2.id)
            foreign_id, live_id, owner_id, stranger_id = (
                foreign.id, live.id, owner.id, stranger.id
            )
            assert await cases.select_active_case(user=owner, case_id=live_id) is not None
            await db.commit()

        async with factory() as db:
            cases = CaseService(db)
            owner = await db.get(User, owner_id)
            selected_before = owner.active_case_id
            assert selected_before == live_id
            for case_id in owned_ids:
                target = await cases.get_terminal_case_for_user(
                    user_id=owner_id, case_id=case_id
                )
                assert target is not None and target.id == case_id
                assert target.client_id == owner_id
            for bad_id in (foreign_id, live_id, 987654321):
                assert await cases.get_terminal_case_for_user(
                    user_id=owner_id, case_id=bad_id
                ) is None
            assert await cases.get_terminal_case_for_user(
                user_id=stranger_id, case_id=owned_ids[0]
            ) is None
            assert owner.active_case_id == selected_before
            assert (await cases.get_active_case_for_user(owner_id)).id == live_id
            assert (await cases.select_active_case(
                user=owner, case_id=owned_ids[0]
            )) is None
            assert owner.active_case_id == selected_before
            assert not db.dirty and not db.new
            await db.rollback()
    finally:
        await engine.dispose()
