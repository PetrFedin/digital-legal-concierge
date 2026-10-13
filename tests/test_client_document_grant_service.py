"""Grant state: separate staff tokens, issue replacement and one-time claim."""
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.models import Base
from app.models.client_document_grant import ClientDocumentGrant
from app.domain.documents.client_grant_service import ClientDocumentGrantService, ClientGrantDenied
from test_client_archive_document_preflight import seed


@pytest.mark.asyncio
async def test_issue_claim_retry_and_cross_account(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'grants.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            owner, stranger, case, foreign, active, docs = await seed(db)
            service = ClientDocumentGrantService(db)
            issued = await service.issue(
                user_id=owner.id, case_id=case.id, document_id=docs[0].id,
                approval_reference="SECURITY-TEST-ONLY"
            )
            await db.commit()
            row = await db.get(ClientDocumentGrant, 1)
            assert row.status == "ISSUED"
            assert issued.secret not in row.token_digest
            assert row.channel == "PRIVATE_PORTAL"
            with pytest.raises(ClientGrantDenied):
                await service.claim(user_id=stranger.id, public_id=issued.public_id, secret=issued.secret)
            with pytest.raises(ClientGrantDenied):
                await service.claim(user_id=owner.id, public_id=issued.public_id, secret="invalid")
            await service.claim(user_id=owner.id, public_id=issued.public_id, secret=issued.secret)
            await db.commit()
            await db.refresh(row)
            assert row.status == "CLAIMED"
            assert row.claimed_at is not None
            with pytest.raises(ClientGrantDenied):
                await service.claim(user_id=owner.id, public_id=issued.public_id, secret=issued.secret)
            second = await service.issue(
                user_id=owner.id, case_id=case.id, document_id=docs[0].id,
                approval_reference="SECURITY-TEST-ONLY"
            )
            third = await service.issue(
                user_id=owner.id, case_id=case.id, document_id=docs[0].id,
                approval_reference="SECURITY-TEST-ONLY"
            )
            await db.commit()
            previous = await db.get(ClientDocumentGrant, 2)
            assert previous.status == "REVOKED"
            with pytest.raises(ClientGrantDenied):
                await service.claim(user_id=owner.id, public_id=second.public_id, secret=second.secret)
            assert third.public_id != second.public_id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_claim_revoked_when_retention_approved(tmp_path):
    from datetime import datetime, timezone
    from app.models.case_retention import CaseRetentionRecord
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'revocation.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            owner, stranger, case, foreign, active, docs = await seed(db)
            service = ClientDocumentGrantService(db)
            grant = await service.issue(user_id=owner.id, case_id=case.id,
                                        document_id=docs[0].id, approval_reference="SECURITY-TEST-ONLY")
            db.add(CaseRetentionRecord(case_id=case.id, status="APPROVED",
                                       retention_due_at=datetime.now(timezone.utc)))
            await db.commit()
            with pytest.raises(ClientGrantDenied):
                await service.claim(user_id=owner.id, public_id=grant.public_id, secret=grant.secret)
    finally:
        await engine.dispose()
