from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base
from app.models.document import Document
from app.security.audit_integrity import verify_audit_chain
from app.security.document_encryption import (
    ENCRYPTION_STATUS,
    LEGACY_STATUS,
    DocumentEncryptionError,
    decrypt_file_bytes,
    encrypt_file,
    is_encrypted_file,
    rotate_encrypted_file,
)
from app.security.document_key_rotation import migrate_document_encryption

DOC_OLD = "document-old-" + "a" * 40
DOC_NEW = "document-new-" + "b" * 40
AUDIT_KEY = "audit-document-" + "c" * 40
HMAC_KEY = "hmac-document-" + "d" * 40


def configure_keys(monkeypatch, *, key_id: str = "documents-old", key: str = DOC_OLD):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "max_document_upload_mb", 20)
    monkeypatch.setattr(settings, "allow_legacy_security_key_fallback", False)
    monkeypatch.setattr(settings, "document_encryption_key_id", key_id)
    monkeypatch.setattr(settings, "document_encryption_key", key)
    monkeypatch.setattr(settings, "document_encryption_previous_keys", "")
    monkeypatch.setattr(settings, "audit_integrity_key_id", "audit-doc-test")
    monkeypatch.setattr(settings, "audit_integrity_key", AUDIT_KEY)
    monkeypatch.setattr(settings, "audit_integrity_previous_keys", "")
    monkeypatch.setattr(settings, "security_hmac_key_id", "hmac-doc-test")
    monkeypatch.setattr(settings, "security_hmac_key", HMAC_KEY)
    monkeypatch.setattr(settings, "security_hmac_previous_keys", "")


def test_encrypt_decrypt_and_detect_ciphertext_tampering(tmp_path, monkeypatch):
    configure_keys(monkeypatch)
    plaintext = b"%PDF-1.4\nconfidential legal evidence\n%%EOF\n"
    source = tmp_path / "source.pdf"
    target = tmp_path / "document.dlcenc"
    source.write_bytes(plaintext)
    expected_sha256 = hashlib.sha256(plaintext).hexdigest()

    metadata = encrypt_file(source, target, expected_sha256=expected_sha256)
    decrypted, verified = decrypt_file_bytes(target, expected_sha256=expected_sha256)

    assert metadata.key_id == "documents-old"
    assert target.read_bytes() != plaintext
    assert is_encrypted_file(target)
    assert decrypted == plaintext
    assert verified.sha256 == expected_sha256

    corrupted = bytearray(target.read_bytes())
    corrupted[-1] ^= 1
    target.write_bytes(corrupted)
    with pytest.raises(DocumentEncryptionError, match="Целостность"):
        decrypt_file_bytes(target, expected_sha256=expected_sha256)


def test_document_key_rotation_uses_previous_key_only_during_grace(
    tmp_path,
    monkeypatch,
):
    configure_keys(monkeypatch)
    plaintext = b"passport and payment evidence"
    source = tmp_path / "source.bin"
    source.write_bytes(plaintext)
    rotated = tmp_path / "rotated.dlcenc"
    legacy = tmp_path / "legacy.dlcenc"
    encrypt_file(source, rotated)
    encrypt_file(source, legacy)

    monkeypatch.setattr(settings, "document_encryption_key_id", "documents-new")
    monkeypatch.setattr(settings, "document_encryption_key", DOC_NEW)
    monkeypatch.setattr(
        settings,
        "document_encryption_previous_keys",
        f"documents-old:{DOC_OLD}",
    )
    metadata = rotate_encrypted_file(rotated)
    assert metadata.key_id == "documents-new"
    assert decrypt_file_bytes(rotated)[0] == plaintext
    assert decrypt_file_bytes(legacy)[0] == plaintext

    monkeypatch.setattr(settings, "document_encryption_previous_keys", "")
    assert decrypt_file_bytes(rotated)[0] == plaintext
    with pytest.raises(DocumentEncryptionError, match="отсутствует"):
        decrypt_file_bytes(legacy)


@pytest.mark.asyncio
async def test_legacy_plaintext_is_migrated_in_place_and_audited(
    tmp_path,
    monkeypatch,
):
    configure_keys(monkeypatch)
    storage_root = tmp_path / "storage"
    case_dir = storage_root / "cases" / "7"
    case_dir.mkdir(parents=True)
    plaintext = b"%PDF-1.4\nlegacy document\n%%EOF\n"
    path = case_dir / "legacy.pdf"
    path.write_bytes(plaintext)
    sha256 = hashlib.sha256(plaintext).hexdigest()
    monkeypatch.setattr(settings, "storage_dir", str(storage_root))

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'documents.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        db.add(
            Document(
                case_id=7,
                uploaded_by_user_id=None,
                document_type="DDU",
                title="ДДУ",
                file_name="legacy.pdf",
                file_path=str(path),
                mime_type="application/pdf",
                file_size=len(plaintext),
                sha256=sha256,
                detected_type="pdf",
                security_status="VERIFIED",
                scanned_at=datetime.now(timezone.utc),
                encryption_status=LEGACY_STATUS,
                version=1,
                status="UPLOADED",
                is_required=False,
            )
        )
        await db.commit()

    async with factory() as db:
        result = await migrate_document_encryption(db)
        await db.commit()
        document = await db.get(Document, 1)
        integrity = await verify_audit_chain(db)

        assert result == {"encrypted": 1, "rotated": 0, "repaired": 0, "failed": 0}
        assert document.encryption_status == ENCRYPTION_STATUS
        assert document.encryption_key_id == "documents-old"
        assert document.encryption_error is None
        assert document.encrypted_at is not None
        assert is_encrypted_file(path)
        assert path.read_bytes() != plaintext
        assert decrypt_file_bytes(path, expected_sha256=sha256)[0] == plaintext
        assert integrity["ok"] is True
        assert integrity["checked_count"] == 1

    await engine.dispose()
