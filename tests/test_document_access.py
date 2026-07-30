from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base
from app.models.admin_user import AdminUser
from app.models.case import Case
from app.models.document import Document
from app.models.document_access_grant import DocumentAccessGrant
from app.models.lawyer import Lawyer
from app.models.user import User
from app.security.access_control import create_access_token
from app.security.document_access import (
    DocumentAccessError,
    cleanup_document_access_grants,
    consume_document_grant,
    issue_document_grant,
    load_authorized_document,
    resolve_document_actor,
)
from app.security.document_encryption import ENCRYPTION_STATUS, FORMAT_V2, encrypt_file
from app.storage import LocalStorageService

SESSION_KEY = "session-document-access-" + "a" * 40
HMAC_KEY = "hmac-document-access-" + "b" * 40
DOCUMENT_KEY = "encryption-document-access-" + "c" * 40


def configure_security(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "session_signing_key_id", "session-test")
    monkeypatch.setattr(settings, "session_signing_key", SESSION_KEY)
    monkeypatch.setattr(settings, "session_signing_previous_keys", "")
    monkeypatch.setattr(settings, "security_hmac_key_id", "hmac-test")
    monkeypatch.setattr(settings, "security_hmac_key", HMAC_KEY)
    monkeypatch.setattr(settings, "security_hmac_previous_keys", "")
    monkeypatch.setattr(settings, "document_encryption_key_id", "documents-test")
    monkeypatch.setattr(settings, "document_encryption_key", DOCUMENT_KEY)
    monkeypatch.setattr(settings, "document_encryption_previous_keys", "")
    monkeypatch.setattr(settings, "allow_legacy_security_key_fallback", False)
    monkeypatch.setattr(settings, "document_access_grant_ttl_seconds", 120)
    monkeypatch.setattr(settings, "document_access_max_active_grants", 2)


async def seed_document(db, tmp_path, *, assigned_lawyer_id=None):
    client = User(telegram_id=1001, full_name="Клиент")
    db.add(client)
    await db.flush()
    case = Case(
        case_number="CASE-ACCESS-1",
        client_id=client.id,
        route="M1",
        status="M1_LAWYER_REVIEW",
        assigned_lawyer_id=assigned_lawyer_id,
    )
    db.add(case)
    await db.flush()

    plaintext = b"%PDF-1.4\nconfidential evidence\n%%EOF\n"
    sha256 = hashlib.sha256(plaintext).hexdigest()
    source = tmp_path / "source.pdf"
    target = tmp_path / "storage" / "cases" / str(case.id) / "document.dlcenc"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(plaintext)
    encryption = encrypt_file(source, target, expected_sha256=sha256)
    document = Document(
        case_id=case.id,
        uploaded_by_user_id=client.id,
        document_type="DDU",
        title="ДДУ",
        file_name="договор.pdf",
        file_path=str(target),
        mime_type="application/pdf",
        file_size=len(plaintext),
        sha256=sha256,
        detected_type="pdf",
        security_status="VERIFIED",
        scanned_at=datetime.now(timezone.utc),
        encryption_status=ENCRYPTION_STATUS,
        encryption_key_id=encryption.key_id,
        encryption_format_version=encryption.format_version,
        encryption_envelope_id=encryption.envelope_id,
        encrypted_data_key=encryption.encrypted_data_key,
        encrypted_data_key_nonce=encryption.encrypted_data_key_nonce,
        encrypted_at=encryption.encrypted_at,
        version=1,
        status="ON_REVIEW",
        is_required=True,
    )
    db.add(document)
    await db.flush()
    return case, document, plaintext


def document_envelope(document):
    return {
        "encryption_key_id": document.encryption_key_id,
        "encryption_envelope_id": document.encryption_envelope_id,
        "encrypted_data_key": document.encrypted_data_key,
        "encrypted_data_key_nonce": document.encrypted_data_key_nonce,
    }


@pytest.mark.asyncio
async def test_grant_is_bound_to_session_consumed_once_and_decrypts(
    tmp_path,
    monkeypatch,
):
    configure_security(monkeypatch, tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'access.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        admin = AdminUser(
            full_name="Администратор",
            username="admin-access",
            email="admin-access@example.test",
            password_hash="unused",
            role="admin",
            is_active=True,
            session_version=1,
        )
        db.add(admin)
        await db.flush()
        case, document, plaintext = await seed_document(db, tmp_path)
        token = create_access_token(
            admin.id,
            admin.username,
            admin.role,
            session_version=admin.session_version,
        )
        actor = await resolve_document_actor(db, token)
        authorized_document, authorized_case = await load_authorized_document(
            db,
            actor=actor,
            document_id=document.id,
        )
        issued = await issue_document_grant(
            db,
            actor=actor,
            document=authorized_document,
            case=authorized_case,
            client_address="127.0.0.1",
        )
        await db.commit()

        grant, consumed_document, consumed_case = await consume_document_grant(
            db,
            actor=actor,
            public_id=issued.grant.public_id,
            secret=issued.secret,
        )
        await db.commit()
        assert grant.used_at is not None
        assert consumed_document.id == document.id
        assert consumed_case.id == case.id
        assert plaintext not in open(document.file_path, "rb").read()
        assert (
            LocalStorageService().read_document_bytes(
                document.file_path,
                expected_sha256=document.sha256,
                **document_envelope(document),
            )
            == plaintext
        )

        with pytest.raises(DocumentAccessError) as reused:
            await consume_document_grant(
                db,
                actor=actor,
                public_id=issued.grant.public_id,
                secret=issued.secret,
            )
        assert reused.value.reason == "grant_reused"

        other_token = create_access_token(
            admin.id,
            admin.username,
            admin.role,
            session_version=admin.session_version,
        )
        other_actor = await resolve_document_actor(db, other_token)
        second = await issue_document_grant(
            db,
            actor=actor,
            document=document,
            case=case,
            client_address="127.0.0.1",
        )
        await db.commit()
        with pytest.raises(DocumentAccessError) as wrong_session:
            await consume_document_grant(
                db,
                actor=other_actor,
                public_id=second.grant.public_id,
                secret=second.secret,
            )
        assert wrong_session.value.reason == "session_mismatch"

    await engine.dispose()


@pytest.mark.asyncio
async def test_destroyed_or_incomplete_envelope_cannot_receive_access_grant(
    tmp_path,
    monkeypatch,
):
    configure_security(monkeypatch, tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'destroyed.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        admin = AdminUser(
            full_name="Администратор",
            username="destroyed-admin",
            email="destroyed@example.test",
            password_hash="unused",
            role="admin",
            is_active=True,
            session_version=1,
        )
        db.add(admin)
        await db.flush()
        _, document, _ = await seed_document(db, tmp_path)
        actor = await resolve_document_actor(
            db,
            create_access_token(
                admin.id,
                admin.username,
                admin.role,
                session_version=1,
            ),
        )

        document.data_key_destroyed_at = datetime.now(timezone.utc)
        document.encrypted_data_key = None
        document.encrypted_data_key_nonce = None
        await db.flush()
        with pytest.raises(DocumentAccessError) as destroyed:
            await load_authorized_document(db, actor=actor, document_id=document.id)
        assert destroyed.value.reason == "document_key_destroyed"

        document.data_key_destroyed_at = None
        await db.flush()
        with pytest.raises(DocumentAccessError) as incomplete:
            await load_authorized_document(db, actor=actor, document_id=document.id)
        assert incomplete.value.reason == "envelope_migration_required"

    await engine.dispose()


@pytest.mark.asyncio
async def test_assigned_lawyer_access_and_unassigned_lawyer_denial(tmp_path, monkeypatch):
    configure_security(monkeypatch, tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'lawyer-access.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        lawyer = Lawyer(
            full_name="Назначенный юрист",
            email="lawyer@example.test",
            is_active=True,
        )
        other_lawyer = Lawyer(
            full_name="Другой юрист",
            email="other-lawyer@example.test",
            is_active=True,
        )
        db.add_all([lawyer, other_lawyer])
        await db.flush()
        assigned_account = AdminUser(
            full_name=lawyer.full_name,
            username="assigned-lawyer",
            email=lawyer.email,
            password_hash="unused",
            role="lawyer",
            is_active=True,
            session_version=1,
        )
        other_account = AdminUser(
            full_name=other_lawyer.full_name,
            username="other-lawyer",
            email=other_lawyer.email,
            password_hash="unused",
            role="lawyer",
            is_active=True,
            session_version=1,
        )
        db.add_all([assigned_account, other_account])
        await db.flush()
        case, document, _ = await seed_document(
            db,
            tmp_path,
            assigned_lawyer_id=lawyer.id,
        )

        assigned_actor = await resolve_document_actor(
            db,
            create_access_token(
                assigned_account.id,
                assigned_account.username,
                assigned_account.role,
                session_version=1,
            ),
        )
        loaded, loaded_case = await load_authorized_document(
            db,
            actor=assigned_actor,
            document_id=document.id,
        )
        assert loaded.id == document.id
        assert loaded_case.id == case.id

        other_actor = await resolve_document_actor(
            db,
            create_access_token(
                other_account.id,
                other_account.username,
                other_account.role,
                session_version=1,
            ),
        )
        with pytest.raises(DocumentAccessError) as denied:
            await load_authorized_document(
                db,
                actor=other_actor,
                document_id=document.id,
            )
        assert denied.value.reason == "lawyer_not_assigned"

    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_and_used_grants_are_cleaned_up(tmp_path, monkeypatch):
    configure_security(monkeypatch, tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cleanup.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        admin = AdminUser(
            full_name="Администратор",
            username="cleanup-admin",
            email="cleanup@example.test",
            password_hash="unused",
            role="admin",
            is_active=True,
            session_version=1,
        )
        db.add(admin)
        await db.flush()
        case, document, _ = await seed_document(db, tmp_path)
        actor = await resolve_document_actor(
            db,
            create_access_token(
                admin.id,
                admin.username,
                admin.role,
                session_version=1,
            ),
        )
        issued = await issue_document_grant(
            db,
            actor=actor,
            document=document,
            case=case,
            client_address=None,
        )
        issued.grant.expires_at = datetime.now(timezone.utc) - timedelta(days=2)
        await db.commit()

        removed = await cleanup_document_access_grants(db)
        await db.commit()
        assert removed == 1
        assert await db.get(DocumentAccessGrant, issued.grant.id) is None

    await engine.dispose()
