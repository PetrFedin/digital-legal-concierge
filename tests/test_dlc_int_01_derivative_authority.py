from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.domain.retention.case_retention_service import CaseRetentionService
from app.models import Base
from app.models.admin_user import AdminUser
from app.models.case import Case
from app.models.document import Document
from app.models.document_derivative import (
    DERIVATIVE_READY,
    DERIVATIVE_SANITIZED_PDF,
    DocumentDerivative,
)
from app.models.user import User
from app.security.document_access import DocumentActor, load_authorized_derivative
from app.security.document_encryption import (
    ENCRYPTION_STATUS,
    FORMAT_V2,
    decrypt_file_bytes,
    encrypt_bytes,
)
from app.security.document_key_rotation import migrate_document_derivative_encryption
from app.storage import LocalStorageService


OLD_KEY = "dlc-int-01-old-key-" + "a" * 40
NEW_KEY = "dlc-int-01-new-key-" + "b" * 40
AUDIT_KEY = "dlc-int-01-audit-" + "c" * 40
HMAC_KEY = "dlc-int-01-hmac-" + "d" * 40


def configure_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "document_encryption_key_id", "documents-old")
    monkeypatch.setattr(settings, "document_encryption_key", OLD_KEY)
    monkeypatch.setattr(settings, "document_encryption_previous_keys", "")
    monkeypatch.setattr(settings, "allow_legacy_security_key_fallback", False)
    monkeypatch.setattr(settings, "audit_integrity_key_id", "audit-int-01")
    monkeypatch.setattr(settings, "audit_integrity_key", AUDIT_KEY)
    monkeypatch.setattr(settings, "audit_integrity_previous_keys", "")
    monkeypatch.setattr(settings, "security_hmac_key_id", "hmac-int-01")
    monkeypatch.setattr(settings, "security_hmac_key", HMAC_KEY)
    monkeypatch.setattr(settings, "security_hmac_previous_keys", "")


async def seed_authority(db, storage: LocalStorageService):
    user = User(telegram_id=920001, full_name="Derivative authority client")
    admin = AdminUser(
        full_name="Derivative Admin",
        username="derivative-admin",
        email="derivative-admin@example.test",
        password_hash="unused",
        role="admin",
        is_active=True,
        session_version=1,
    )
    db.add_all([user, admin])
    await db.flush()
    case = Case(
        case_number="DER-AUTH-1",
        client_id=user.id,
        route="M1",
        status="M1_DOCUMENTS_COLLECTION",
    )
    db.add(case)
    await db.flush()

    source_payload = b"%PDF-1.4\nsource evidence\n%%EOF\n"
    source_sha = hashlib.sha256(source_payload).hexdigest()
    source_target = (
        storage.base_dir / "cases" / str(case.id) / ("1" * 32 + ".dlcenc")
    )
    source_target.parent.mkdir(parents=True, exist_ok=True)
    source_meta = encrypt_bytes(
        source_payload,
        source_target,
        expected_sha256=source_sha,
    )
    source = Document(
        case_id=case.id,
        uploaded_by_user_id=user.id,
        document_type="DDU",
        title="ДДУ",
        file_name="source.pdf",
        file_path=storage.storage_key_for_case_path(source_target, case_id=case.id),
        mime_type="application/pdf",
        file_size=len(source_payload),
        sha256=source_sha,
        detected_type="pdf",
        security_status="VERIFIED",
        scanned_at=datetime.now(timezone.utc),
        encryption_status=ENCRYPTION_STATUS,
        encryption_key_id=source_meta.key_id,
        encryption_format_version=source_meta.format_version,
        encryption_envelope_id=source_meta.envelope_id,
        encrypted_data_key=source_meta.encrypted_data_key,
        encrypted_data_key_nonce=source_meta.encrypted_data_key_nonce,
        encrypted_at=source_meta.encrypted_at,
        status="UPLOADED",
        version=1,
    )
    db.add(source)
    await db.flush()

    derivative_payload = b"%PDF-1.4\nderived evidence\n%%EOF\n"
    derivative_sha = hashlib.sha256(derivative_payload).hexdigest()
    derivative_target = (
        storage.base_dir / "cases" / str(case.id) / ("2" * 32 + ".dlcenc")
    )
    derivative_meta = encrypt_bytes(
        derivative_payload,
        derivative_target,
        expected_sha256=derivative_sha,
    )
    derivative = DocumentDerivative(
        case_id=case.id,
        source_document_id=source.id,
        derivative_type=DERIVATIVE_SANITIZED_PDF,
        status=DERIVATIVE_READY,
        source_sha256=source_sha,
        file_path=storage.storage_key_for_case_path(
            derivative_target,
            case_id=case.id,
        ),
        mime_type="application/pdf",
        file_size=len(derivative_payload),
        sha256=derivative_sha,
        tool_name="pikepdf",
        tool_version="10.16.0",
        recipe_id="dlc-int-01-pdf-sanitize-v1",
        provenance={"source_document_id": source.id},
        page_count=1,
        has_usable_text=True,
        encryption_status=ENCRYPTION_STATUS,
        encryption_key_id=derivative_meta.key_id,
        encryption_format_version=FORMAT_V2,
        encryption_envelope_id=derivative_meta.envelope_id,
        encrypted_data_key=derivative_meta.encrypted_data_key,
        encrypted_data_key_nonce=derivative_meta.encrypted_data_key_nonce,
        encrypted_at=derivative_meta.encrypted_at,
    )
    db.add(derivative)
    await db.flush()
    return admin, case, source, derivative, derivative_target


@pytest.mark.asyncio
async def test_derivative_access_reuses_source_document_case_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_keys(monkeypatch, tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'access.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        storage = LocalStorageService()
        admin, case, source, derivative, _ = await seed_authority(db, storage)
        actor = DocumentActor(
            account=admin,
            payload={"jti": "session-1", "sv": 1, "roles": ["admin"]},
            role="admin",
        )

        loaded, loaded_source, loaded_case = await load_authorized_derivative(
            db,
            actor=actor,
            derivative_id=derivative.id,
        )

        assert loaded.id == derivative.id
        assert loaded_source.id == source.id
        assert loaded_case.id == case.id

        derivative.source_sha256 = "0" * 64
        await db.flush()
        with pytest.raises(Exception, match="Связь производного документа"):
            await load_authorized_derivative(
                db,
                actor=actor,
                derivative_id=derivative.id,
            )

    await engine.dispose()


def test_retention_preflights_and_deletes_derivative_case_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_keys(monkeypatch, tmp_path)
    storage = LocalStorageService()
    case_id = 42
    target = storage.base_dir / "cases" / str(case_id) / ("3" * 32 + ".dlcenc")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"encrypted-derivative")
    derivative = DocumentDerivative(
        id=5,
        case_id=case_id,
        source_document_id=1,
        derivative_type=DERIVATIVE_SANITIZED_PDF,
        status=DERIVATIVE_READY,
        source_sha256="a" * 64,
        file_path=storage.storage_key_for_case_path(target, case_id=case_id),
        tool_name="pikepdf",
        tool_version="10.16.0",
        recipe_id="dlc-int-01-pdf-sanitize-v1",
        provenance={},
    )
    service = CaseRetentionService(None)  # type: ignore[arg-type]

    service._preflight_derivatives([derivative])
    service._delete_derivative_files([derivative])
    service._delete_derivative_files([derivative])

    assert not target.exists()


@pytest.mark.asyncio
async def test_derivative_master_key_rotation_rewraps_without_rewriting_ciphertext(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_keys(monkeypatch, tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rotation.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        storage = LocalStorageService()
        _, _, _, derivative, target = await seed_authority(db, storage)
        await db.commit()
        ciphertext_before = target.read_bytes()
        old_envelope = derivative.encryption_envelope_id

        monkeypatch.setattr(settings, "document_encryption_key_id", "documents-new")
        monkeypatch.setattr(settings, "document_encryption_key", NEW_KEY)
        monkeypatch.setattr(
            settings,
            "document_encryption_previous_keys",
            f"documents-old:{OLD_KEY}",
        )

        result = await migrate_document_derivative_encryption(db)
        await db.commit()
        await db.refresh(derivative)

        assert result["rewrapped"] == 1
        assert derivative.encryption_key_id == "documents-new"
        assert derivative.encryption_envelope_id == old_envelope
        assert target.read_bytes() == ciphertext_before
        plaintext, _ = decrypt_file_bytes(
            target,
            expected_sha256=derivative.sha256,
            encryption_key_id=derivative.encryption_key_id,
            encryption_envelope_id=derivative.encryption_envelope_id,
            encrypted_data_key=derivative.encrypted_data_key,
            encrypted_data_key_nonce=derivative.encrypted_data_key_nonce,
        )
        assert b"derived evidence" in plaintext

    await engine.dispose()
