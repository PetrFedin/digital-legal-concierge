"""Real PostgreSQL security gate for client-only archive document grants.

The GitHub Actions runner uses a disposable migrated database; no production
case, user, provider token, encrypted file, or transport endpoint is touched.
SQLite tests are intentionally not evidence of PostgreSQL row-lock behavior.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.documents.client_grant_service import (
    ClientDocumentGrantService,
    ClientGrantDenied,
)
from app.models.case import Case
from app.models.case_retention import CaseRetentionRecord
from app.models.client_document_grant import ClientDocumentGrant
from app.models.document import Document
from app.models.user import User


pytestmark = pytest.mark.asyncio
_APPROVAL = "PG-TEST-SYNTHETIC-NOT-A-LEGAL-APPROVAL"


async def _synthetic_archived_document(factory):
    token = uuid4().hex
    suffix = int(token[:10], 16)
    async with factory() as db:
        owner = User(telegram_id=700_000_000_000_000 + suffix)
        stranger = User(telegram_id=800_000_000_000_000 + suffix)
        db.add_all([owner, stranger])
        await db.flush()

        archived = Case(
            case_number=f"PG-GRANT-{token[:18]}",
            client_id=owner.id,
            route="M1",
            status="M1_CLOSED",
            title="Synthetic archived test matter",
        )
        db.add(archived)
        await db.flush()
        document = Document(
            case_id=archived.id,
            document_type="DDU",
            title="Synthetic admission document",
            file_name="synthetic.pdf",
            file_path="/not-an-actual-document/synthetic.pdf",
            version=1,
            file_size=100,
            mime_type="application/pdf",
            sha256="a" * 64,
            security_status="VERIFIED",
            encryption_status="ENCRYPTED",
            encryption_format_version=2,
            encryption_key_id="documents-v1",
            encryption_envelope_id=uuid4().hex,
            encrypted_data_key="dummy-key-never-decrypted",
            encrypted_data_key_nonce="b" * 32,
            encrypted_at=datetime.now(timezone.utc),
        )
        db.add(document)
        await db.flush()
        ids = owner.id, stranger.id, archived.id, document.id
        await db.commit()
        return ids


def _postgres_url():
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.skip("Disposable PostgreSQL runner required for grant race evidence")
    return url


async def test_postgresql_parallel_issue_and_double_claim_are_single_use():
    engine = create_async_engine(
        _postgres_url(), pool_size=5, max_overflow=0, pool_timeout=15,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        owner_id, stranger_id, case_id, doc_id = await _synthetic_archived_document(factory)

        async def issue_once():
            async with factory() as db:
                issued = await ClientDocumentGrantService(db).issue(
                    user_id=owner_id,
                    case_id=case_id,
                    document_id=doc_id,
                    approval_reference=_APPROVAL,
                )
                await db.commit()
                return issued

        # PostgreSQL FOR UPDATE on User must serialize issuance. The second
        # successful issue revokes the first before creating its replacement.
        first, second = await asyncio.wait_for(
            asyncio.gather(issue_once(), issue_once()), timeout=25,
        )
        assert first.public_id != second.public_id
        async with factory() as db:
            grants = (await db.scalars(
                select(ClientDocumentGrant)
                .where(ClientDocumentGrant.document_id == doc_id)
                .order_by(ClientDocumentGrant.id)
            )).all()
            assert len(grants) == 2
            assert {g.status for g in grants} == {"ISSUED", "REVOKED"}
            current_id = next(g.public_id for g in grants if g.status == "ISSUED")
            active = first if first.public_id == current_id else second
            revoked = second if active is first else first

        async with factory() as db:
            service = ClientDocumentGrantService(db)
            with pytest.raises(ClientGrantDenied):
                await service.claim(
                    user_id=stranger_id, public_id=active.public_id,
                    secret=active.secret,
                )
            with pytest.raises(ClientGrantDenied):
                await service.claim(
                    user_id=owner_id, public_id=active.public_id,
                    secret=active.secret + "wrong",
                )
            with pytest.raises(ClientGrantDenied):
                await service.claim(
                    user_id=owner_id, public_id=revoked.public_id,
                    secret=revoked.secret,
                )
            await db.rollback()

        async def claim_once():
            async with factory() as db:
                try:
                    await ClientDocumentGrantService(db).claim(
                        user_id=owner_id, public_id=active.public_id,
                        secret=active.secret,
                    )
                    await db.commit()
                    return True
                except ClientGrantDenied:
                    await db.rollback()
                    return False

        # Two sessions can both see ISSUED before entering the conditional
        # UPDATE. At most one transaction may commit the CLAIMED state.
        results = await asyncio.wait_for(
            asyncio.gather(claim_once(), claim_once()), timeout=25,
        )
        assert sorted(results) == [False, True]
        async with factory() as db:
            grants = (await db.scalars(
                select(ClientDocumentGrant).where(
                    ClientDocumentGrant.document_id == doc_id,
                )
            )).all()
            assert len(grants) == 2
            assert sum(g.status == "CLAIMED" for g in grants) == 1
            assert sum(g.status == "REVOKED" for g in grants) == 1
            assert all(g.claimed_at is not None for g in grants if g.status == "CLAIMED")
            with pytest.raises(ClientGrantDenied):
                await ClientDocumentGrantService(db).claim(
                    user_id=owner_id, public_id=active.public_id,
                    secret=active.secret,
                )
    finally:
        await engine.dispose()


async def test_postgresql_claim_rechecks_mutable_ownership_and_erasure():
    engine = create_async_engine(_postgres_url(), pool_size=3, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        owner_id, stranger_id, case_id, doc_id = await _synthetic_archived_document(factory)
        async with factory() as db:
            issued = await ClientDocumentGrantService(db).issue(
                user_id=owner_id, case_id=case_id,
                document_id=doc_id, approval_reference=_APPROVAL,
            )
            await db.commit()

        async def denied():
            async with factory() as db:
                with pytest.raises(ClientGrantDenied):
                    await ClientDocumentGrantService(db).claim(
                        user_id=owner_id,
                        public_id=issued.public_id, secret=issued.secret,
                    )
                await db.rollback()

        # A valid token does not survive a change to the exact source digest.
        async with factory() as db:
            source = await db.get(Document, doc_id)
            source.sha256 = "c" * 64
            await db.commit()
        await denied()

        # Nor may it bypass a case reopening / nonterminal status.
        async with factory() as db:
            (await db.get(Document, doc_id)).sha256 = "a" * 64
            (await db.get(Case, case_id)).status = "M1_DOCUMENTS_PENDING"
            await db.commit()
        await denied()

        # A blocked client may not use an otherwise valid token.
        async with factory() as db:
            (await db.get(Case, case_id)).status = "M1_CLOSED"
            (await db.get(User, owner_id)).is_blocked = True
            await db.commit()
        await denied()

        # Once erasure has been approved, access remains denied even before
        # document/financial-retention rows have physically been deleted.
        async with factory() as db:
            (await db.get(User, owner_id)).is_blocked = False
            db.add(CaseRetentionRecord(
                case_id=case_id, status="APPROVED",
                retention_due_at=datetime.now(timezone.utc),
            ))
            await db.commit()
        await denied()

        async with factory() as db:
            row = await db.scalar(
                select(ClientDocumentGrant).where(
                    ClientDocumentGrant.public_id == issued.public_id,
                )
            )
            assert row is not None and row.status == "ISSUED"
            assert row.claimed_at is None
            assert row.user_id != stranger_id
            assert row.document_id == doc_id
    finally:
        await engine.dispose()
