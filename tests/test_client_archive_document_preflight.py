"""Contract checks for future client archive retrieval, no bytes exposed."""
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.models import Base
from app.models.user import User
from app.models.case import Case
from app.models.document import Document
from app.models.case_retention import CaseRetentionRecord
from app.domain.documents.client_archive_access import (
    ClientArchiveDocumentAccessService, ClientArchiveDocumentDenied,
)


@pytest.fixture
async def db_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'preflight.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def seed(db):
    from datetime import datetime, timezone
    owner = User(telegram_id=1100000081)
    stranger = User(telegram_id=1100000082)
    db.add_all([owner, stranger])
    await db.flush()
    own = Case(case_number="AR-OWNER", client_id=owner.id, route="M1", status="M1_CLOSED")
    foreign = Case(case_number="AR-FOREIGN", client_id=stranger.id, route="M2", status="M2_CLOSED")
    active = Case(case_number="AR-ACTIVE", client_id=owner.id, route="M1", status="M1_DOCUMENTS_PENDING")
    db.add_all([own, foreign, active])
    await db.flush()
    def doc(case_id, label):
        return Document(
            case_id=case_id, document_type="DDU", title=label,
            file_name="safe.pdf", file_path="/test/never-open.pdf",
            version=1, file_size=100, mime_type="application/pdf",
            sha256="a"*64, security_status="VERIFIED",
            encryption_status="ENCRYPTED", encryption_format_version=2,
            encryption_key_id="documents-v1",
            encryption_envelope_id=f"{case_id:032x}",
            encrypted_data_key="wrapped-data-key",
            encrypted_data_key_nonce="b"*32,
            encrypted_at=datetime.now(timezone.utc),
        )
    docs=[doc(own.id,"Owner"),doc(foreign.id,"Foreign"),doc(active.id,"Active")]
    db.add_all(docs)
    await db.flush()
    return owner,stranger,own,foreign,active,docs


@pytest.mark.asyncio
async def test_owner_preflight_is_metadata_only_and_denies_other_tenant(db_factory):
    async with db_factory() as db:
        owner,stranger,own,foreign,active,docs=await seed(db)
        gate=ClientArchiveDocumentAccessService(db)
        descriptor=await gate.preflight(user_id=owner.id,case_id=own.id,document_id=docs[0].id)
        assert descriptor.case_id == own.id and descriptor.title == "Owner"
        assert not hasattr(descriptor, "file_path")
        for uid,cid,did in [
            (owner.id,foreign.id,docs[1].id),
            (stranger.id,own.id,docs[0].id),
            (owner.id,own.id,docs[1].id),
            (owner.id,active.id,docs[2].id),
            (owner.id,own.id,1234567),
        ]:
            with pytest.raises(ClientArchiveDocumentDenied):
                await gate.preflight(user_id=uid,case_id=cid,document_id=did)


@pytest.mark.asyncio
@pytest.mark.parametrize("state",["APPROVED","EXECUTING","FAILED","COMPLETED"])
async def test_retention_lifecycle_blocks_retrieval_even_if_document_exists(db_factory,state):
    from datetime import datetime, timezone
    async with db_factory() as db:
        owner,_,own,_,_,docs=await seed(db)
        db.add(CaseRetentionRecord(
            case_id=own.id, status=state,
            retention_due_at=datetime.now(timezone.utc),
        ))
        await db.flush()
        with pytest.raises(ClientArchiveDocumentDenied):
            await ClientArchiveDocumentAccessService(db).preflight(
                user_id=owner.id,case_id=own.id,document_id=docs[0].id
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption",[
    {"security_status":"PENDING"},
    {"sha256":"not-a-digest"},
    {"encrypted_data_key":None},
    {"encryption_format_version":1},
    {"data_key_destroyed_at":"destroyed"},
    {"file_size":999999999},
])
async def test_corrupt_unverified_or_revoked_metadata_fail_closed(db_factory,corruption):
    from datetime import datetime, timezone
    async with db_factory() as db:
        owner,_,own,_,_,docs=await seed(db)
        for k,v in corruption.items():
            if k=="data_key_destroyed_at":
                v=datetime.now(timezone.utc)
            setattr(docs[0],k,v)
        await db.flush()
        with pytest.raises(ClientArchiveDocumentDenied):
            await ClientArchiveDocumentAccessService(db).preflight(
                user_id=owner.id,case_id=own.id,document_id=docs[0].id
            )
