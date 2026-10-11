"""DB-backed Telegram acceptance of M2 -> M1 consent and case boundaries.

This exercises actual handlers and database writes; it does not contact Telegram
or a payment provider. All data and payments are synthetic.
"""
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.screens import consultation_results
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.payment import Payment
from app.models.user import User


class FakeMessage:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, *, reply_markup=None):
        self.edits.append((text, reply_markup))


class FakeCallback:
    def __init__(self, *, actor_id: int, data: str):
        self.actor_id = actor_id
        self.data = data
        self.message = FakeMessage()
        self.answers = []

    async def answer(self, message=None, show_alert=False):
        self.answers.append((message, show_alert))


class FakeContext:
    def __init__(self, db):
        self.db = db

    async def get_user_from_callback(self, callback):
        return await self.db.get(User, callback.actor_id)


@asynccontextmanager
async def isolated_db(tmp_path, name):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield factory
    finally:
        await engine.dispose()


async def seed_paid_m2_case(session, *, number: int):
    client = User(telegram_id=980000 + number, full_name=f"Owner {number}")
    stranger = User(telegram_id=990000 + number, full_name=f"Stranger {number}")
    session.add_all([client, stranger])
    await session.flush()

    case = Case(
        case_number=f"M2-CONSENT-{number}",
        client_id=client.id,
        route="M2",
        status=CaseStatus.M2_TO_M1,
        title="Synthetic consultation with a lawyer recommendation",
    )
    session.add(case)
    await session.flush()
    consultation = Consultation(
        case_id=case.id,
        status=ConsultationStatus.DONE,
        decision="to_m1",
        client_description="Check a completed consultation for standard legal recovery.",
        lawyer_result="Assigned lawyer recommended the standard M1 route for this client.",
    )
    document = Document(
        case_id=case.id,
        uploaded_by_user_id=client.id,
        document_type="OTHER",
        title="Synthetic preserved document",
        file_name="synthetic.pdf",
        file_path=f"/synthetic/{case.id}",
        status="UPLOADED",
    )
    payment = Payment(
        case_id=case.id,
        payment_code="M2_CONSULTATION_PAYMENT",
        title="Synthetic paid consultation",
        amount=Decimal("5000.00"),
        currency="RUB",
        status="PAID",
        provider="fake",
        provider_payment_id=f"synthetic-{number}",
    )
    session.add_all([consultation, document, payment])
    await session.commit()
    return client.id, stranger.id, case.id, consultation.id, document.id, payment.id


def callbacks(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]


async def count_events(db, case_id, action):
    return await db.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.entity_type == "case",
            AuditLog.entity_id == case_id,
            AuditLog.action == action,
        )
    )


@pytest.mark.asyncio
async def test_accept_requires_client_review_and_preserves_original_case(monkeypatch, tmp_path):
    monkeypatch.setattr(consultation_results, "BotContextService", FakeContext)
    async with isolated_db(tmp_path, "m2-accept.db") as factory:
        async with factory() as db:
            owner, _, case_id, consultation_id, document_id, payment_id = (
                await seed_paid_m2_case(db, number=1)
            )
            prompt = FakeCallback(
                actor_id=owner, data=f"m2_to_m1_continue:{case_id}"
            )
            await consultation_results.m2_to_m1_continue(prompt, db)
            text, markup = prompt.message.edits[-1]
            assert "согласие" in text.lower()
            assert f"m2_to_m1_accept:{case_id}" in callbacks(markup)
            case = await db.get(Case, case_id)
            assert case.status == CaseStatus.M2_TO_M1
            assert case.consent_status is None

            accept = FakeCallback(actor_id=owner, data=f"m2_to_m1_accept:{case_id}")
            await consultation_results.m2_to_m1_accept(accept, db)
            case = await db.get(Case, case_id)
            assert case.status == CaseStatus.M1_DOCUMENTS_PENDING
            assert case.route == "M1"
            assert case.consent_status == "GIVEN"
            assert case.consent_date is not None
            assert await count_events(db, case_id, "CONSENT_GIVEN") == 1
            assert await count_events(db, case_id, "CASE_TRANSFERRED_TO_M1") == 1
            assert (await db.get(Consultation, consultation_id)).decision == "to_m1"
            assert (await db.get(Consultation, consultation_id)).status == ConsultationStatus.DONE
            assert (await db.get(Document, document_id)).case_id == case_id
            assert (await db.get(Payment, payment_id)).status == "PAID"
            assert (await db.get(Payment, payment_id)).case_id == case_id

            await consultation_results.m2_to_m1_accept(accept, db)
            assert await count_events(db, case_id, "CONSENT_GIVEN") == 1
            assert await count_events(db, case_id, "CASE_TRANSFERRED_TO_M1") == 1


@pytest.mark.asyncio
async def test_foreign_and_unscoped_buttons_never_mutate_case(monkeypatch, tmp_path):
    monkeypatch.setattr(consultation_results, "BotContextService", FakeContext)
    async with isolated_db(tmp_path, "m2-scope.db") as factory:
        async with factory() as db:
            owner, stranger, case_id, _, _, _ = await seed_paid_m2_case(
                db, number=2
            )
            for actor, data in [
                (stranger, f"m2_to_m1_accept:{case_id}"),
                (stranger, f"m2_to_m1_decline:{case_id}"),
                (owner, "m2_to_m1_accept"),
                (owner, "m2_to_m1_continue"),
                (owner, "m2_to_m1_accept:not-an-id"),
                (owner, "m2_to_m1_accept:-1"),
                (owner, f"m2_to_m1_accept:{case_id + 900}"),
            ]:
                await consultation_results.m2_to_m1_accept(
                    FakeCallback(actor_id=actor, data=data), db
                )
            case = await db.get(Case, case_id)
            assert case.status == CaseStatus.M2_TO_M1
            assert case.consent_status is None
            assert await count_events(db, case_id, "CONSENT_GIVEN") == 0


@pytest.mark.asyncio
async def test_refusal_idempotency_stale_acceptance_and_explicit_reconsideration(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(consultation_results, "BotContextService", FakeContext)
    async with isolated_db(tmp_path, "m2-decline.db") as factory:
        async with factory() as db:
            owner, _, case_id, consultation_id, document_id, payment_id = (
                await seed_paid_m2_case(db, number=3)
            )
            refuse = FakeCallback(
                actor_id=owner, data=f"m2_to_m1_decline:{case_id}"
            )
            await consultation_results.m2_to_m1_decline(refuse, db)
            case = await db.get(Case, case_id)
            assert case.route == "M2"
            assert case.status == CaseStatus.M2_TO_M1
            assert case.consent_status == "DECLINED"
            assert case.decline_date is not None
            assert await count_events(db, case_id, "CONSENT_DECLINED") == 1

            await consultation_results.m2_to_m1_decline(refuse, db)
            assert await count_events(db, case_id, "CONSENT_DECLINED") == 1
            old_accept = FakeCallback(
                actor_id=owner, data=f"m2_to_m1_accept:{case_id}"
            )
            await consultation_results.m2_to_m1_accept(old_accept, db)
            assert (await db.get(Case, case_id)).status == CaseStatus.M2_TO_M1

            review = FakeCallback(
                actor_id=owner, data=f"m2_to_m1_continue:{case_id}"
            )
            await consultation_results.m2_to_m1_continue(review, db)
            assert f"m2_to_m1_reconsider:{case_id}" in callbacks(
                review.message.edits[-1][1]
            )
            reconsider = FakeCallback(
                actor_id=owner, data=f"m2_to_m1_reconsider:{case_id}"
            )
            await consultation_results.m2_to_m1_reconsider(reconsider, db)
            assert (await db.get(Case, case_id)).consent_status == "PENDING"
            await consultation_results.m2_to_m1_accept(old_accept, db)

            case = await db.get(Case, case_id)
            assert case.status == CaseStatus.M1_DOCUMENTS_PENDING
            assert case.consent_status == "GIVEN"
            assert case.decline_date is not None
            assert await count_events(db, case_id, "CONSENT_RECONSIDERED") == 1
            assert await count_events(db, case_id, "CONSENT_GIVEN") == 1
            assert (await db.get(Consultation, consultation_id)).case_id == case_id
            assert (await db.get(Document, document_id)).case_id == case_id
            assert (await db.get(Payment, payment_id)).case_id == case_id
