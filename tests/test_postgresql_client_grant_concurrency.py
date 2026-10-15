"""Real PostgreSQL concurrency admission for client document grants."""
import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.domain.documents.client_grant_service import ClientDocumentGrantService, ClientGrantDenied
from app.models.client_document_grant import ClientDocumentGrant
from app.models.user import User
from app.models.case import Case
from app.models.document import Document

pytestmark = pytest.mark.asyncio


async def test_postgresql_parallel_grant_claim_and_reissue():
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.skip("Disposable PostgreSQL required")
    engine = create_async_engine(url, pool_size=5, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    token = uuid4().hex
    try:
        async with factory() as db:
            owner = User(telegram_id=500000000000000+int(token[:10], 16))
            other = User(telegram_id=600000000000000+int(token[10:20], 16))
            db.add_all([owner, other])
            await db.flush()
            case = Case(case_number="GR-"+token[:20], client_id=owner.id, route="M1", status="M1_CLOSED")
            db.add(case)
            await db.flush()
            doc = Document(case_id=case.id, document_type="DDU", title="Test",
                file_name="x.pdf", file_path="/synthetic/not-read", file_size=123,
                mime_type="application/pdf", version=1, sha256="a"*64,
                security_status="VERIFIED", encryption_status="ENCRYPTED",
                encryption_format_version=2, encryption_key_id="documents-test",
                encryption_envelope_id=token,
                encrypted_data_key="wrapped", encrypted_data_key_nonce="b"*32,
                encrypted_at=datetime.now(timezone.utc))
            db.add(doc)
            await db.flush()
            user_id, other_id, case_id, doc_id = owner.id, other.id, case.id, doc.id
            await db.commit()

        async def issue_one():
            async with factory() as db:
                grant = await ClientDocumentGrantService(db).issue(
                    user_id=user_id, case_id=case_id, document_id=doc_id,
                    approval_reference="TEST-APPROVED-CHANNEL")
                await db.commit()
                return grant

        first, second = await asyncio.wait_for(asyncio.gather(issue_one(),issue_one()),25)
        async with factory() as db:
            active = (await db.execute(select(ClientDocumentGrant).where(
                ClientDocumentGrant.user_id==user_id,
                ClientDocumentGrant.document_id==doc_id,
                ClientDocumentGrant.status=="ISSUED"))).scalars().all()
            assert len(active)==1
            live = first if active[0].public_id == first.public_id else second
            stale = second if live is first else first

        async def claim_once(grant, uid):
            async with factory() as db:
                try:
                    await ClientDocumentGrantService(db).claim(
                        user_id=uid, public_id=grant.public_id, secret=grant.secret)
                    await db.commit()
                    return True
                except ClientGrantDenied:
                    await db.rollback()
                    return False

        assert await claim_once(stale,user_id) is False
        assert await claim_once(live,other_id) is False
        results = await asyncio.wait_for(asyncio.gather(
            claim_once(live,user_id), claim_once(live,user_id)),25)
        assert sorted(results)==[False,True]
        async with factory() as db:
            assert await db.scalar(select(func.count(ClientDocumentGrant.id)).where(
                ClientDocumentGrant.user_id==user_id,
                ClientDocumentGrant.status=="CLAIMED"))==1
    finally:
        await engine.dispose()
