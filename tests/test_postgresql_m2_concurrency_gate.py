"""Real PostgreSQL row-lock acceptance gate for a raced M2 -> M1 decision.

The SQLite test suite skips this file. The dedicated PostgreSQL Actions job
runs it after applying Alembic on its disposable service database.
"""
from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.screens import consultation_results
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.user import User


pytestmark = pytest.mark.asyncio


class FakeMessage:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, *, reply_markup=None):
        self.edits.append((text, reply_markup))


class FakeCallback:
    def __init__(self, owner_id, case_id):
        self.actor_id = owner_id
        self.data = f"m2_to_m1_accept:{case_id}"
        self.message = FakeMessage()

    async def answer(self, text=None, show_alert=False):
        return None


class FakeContext:
    def __init__(self, db):
        self.db = db

    async def get_user_from_callback(self, callback):
        return await self.db.get(User, callback.actor_id)


async def test_postgresql_two_concurrent_consents_commit_one_transition(
    monkeypatch,
):
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.skip("Real PostgreSQL service is required for row-lock admission")

    monkeypatch.setattr(consultation_results, "BotContextService", FakeContext)
    engine = create_async_engine(
        url, pool_size=4, max_overflow=0, pool_timeout=10
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            suffix = uuid4().hex[:12]
            owner = User(
                telegram_id=int("8" + str(int(suffix, 16))[:14]),
                full_name="PostgreSQL concurrency synthetic owner",
            )
            db.add(owner)
            await db.flush()
            case = Case(
                case_number=f"PG-CONSENT-{suffix}",
                client_id=owner.id,
                route="M2",
                status=CaseStatus.M2_TO_M1,
                title="Race admission synthetic",
            )
            db.add(case)
            await db.flush()
            db.add(
                Consultation(
                    case_id=case.id,
                    status=ConsultationStatus.DONE,
                    decision="to_m1",
                    lawyer_result="The lawyer recommended standard proceedings.",
                )
            )
            owner_id, case_id = owner.id, case.id
            await db.commit()

        async def accept():
            async with factory() as session:
                callback = FakeCallback(owner_id, case_id)
                await consultation_results.m2_to_m1_accept(callback, session)
                return callback.message.edits[-1][0]

        responses = await asyncio.wait_for(
            asyncio.gather(accept(), accept()), timeout=25
        )
        async with factory() as db:
            case = await db.get(Case, case_id)
            count_given = await db.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id == case_id,
                    AuditLog.action == "CONSENT_GIVEN",
                )
            )
            count_transferred = await db.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id == case_id,
                    AuditLog.action == "CASE_TRANSFERRED_TO_M1",
                )
            )
            assert case.status == CaseStatus.M1_DOCUMENTS_PENDING
            assert case.route == "M1"
            assert case.consent_status == "GIVEN"
            assert count_given == 1
            assert count_transferred == 1
            assert any("сохранены" in response for response in responses)
            assert any("не актуально" in response for response in responses)
    finally:
        await engine.dispose()
