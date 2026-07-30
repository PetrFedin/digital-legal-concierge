from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base
from app.models.document import Document
from app.security.audit_integrity import verify_audit_chain
from app.security.document_encryption import (
    ENCRYPTION_STATUS,
    FORMAT_V1,
    FORMAT_V2,
    LEGACY_STATUS,
    DocumentEncryptionError,
    decrypt_file_bytes,
    encrypt_file,
    encrypt_file_legacy_v1,
    encrypted_format_version,
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


def envelope_kwargs(metadata):
    return {
        "encryption_key_id": metadata.key_id,
        "encryption_envelope_id": metadata.envelope_id,
        "encrypted_data_key": metadata.encrypted_data_key,
        "encrypted_data_key_nonce": metadata.encrypted_data_key_nonce,
    }


def test_v2_encrypt_decrypt_and_detect_ciphertext_tampering(tmp_path, monkeypatch):
    configure_keys(monkeypatch)
    plaintext = b"%PDF-1.4\nconfidential legal evidence\n%%EOF\n"
    source = tmp_path / "source.pdf"
    target = tmp_path / "document.dlcenc"
    source.write_bytes(plaintext)
    expected_sha256 = hashlib.sha256(plaintext).hexdigest()

    metadata = encrypt_file(source, target, expected_sha256=expected_sha256)
    decrypted, verified = decrypt_file_bytes(
        target,
        expected_sha256=expected_sha256,
        **envelope_kwargs(metadata),
    )

    payload = target.read_bytes()
    assert metadata.key_id == "documents-old"
    assert metadata.format_version == FORMAT_V2
    assert metadata.has_usable_envelope
    assert encrypted_format_version(target) == FORMAT_V2
    assert payload != plaintext
    assert DOC_OLD.encode() not in payload
    assert metadata.encrypted_data_key.encode() not in payload
    assert is_encrypted_file(target)
    assert decrypted == plaintext
    assert verified.sha256 == expected_sha256

    corrupted = bytearray(payload)
    corrupted[-1] ^= 1
    target.write_bytes(corrupted)
    with pytest.raises(DocumentEncryptionError, match="Целостность"):
        decrypt_file_bytes(
            target,
            expected_sha256=expected_sha256,
            **envelope_kwargs(metadata),
        )


def test_same_plaintext_gets_independent_ciphertext_and_data_keys(tmp_path, monkeypatch):
    configure_keys(monkeypatch)
    plaintext = b"same confidential document"
    source = tmp_path / "source.bin"
    first = tmp_path / "first.dlcenc"
    second = tmp_path / "second.dlcenc"
    source.write_bytes(plaintext)

    first_metadata = encrypt_file(source, first)
    second_metadata = encrypt_file(source, second)

    assert first.read_bytes() != second.read_bytes()
    assert first_metadata.envelope_id != second_metadata.envelope_id
    assert first_metadata.encrypted_data_key != second_metadata.encrypted_data_key
    assert decrypt_file_bytes(first, **envelope_kwargs(first_metadata))[0] == plaintext
    assert decrypt_file_bytes(second, **envelope_kwargs(second_metadata))[0] == plaintext


def test_missing_or_wrong_envelope_makes_remaining_ciphertext_unreadable(
    tmp_path,
    monkeypatch,
):
    configure_keys(monkeypatch)
    source = tmp_path / "source.bin"
    target = tmp_path / "document.dlcenc"
    source.write_bytes(b"evidence that must become cryptographically inaccessible")
    metadata = encrypt_file(source, target)

    with pytest.raises(DocumentEncryptionError, match="Отсутствует обёрнутый ключ"):
        decrypt_file_bytes(
            target,
            encryption_key_id=metadata.key_id,
            encryption_envelope_id=metadata.envelope_id,
            encrypted_data_key=None,
            encrypted_data_key_nonce=metadata.encrypted_data_key_nonce,
        )
    with pytest.raises(DocumentEncryptionError, match="Envelope документа"):
        decrypt_file_bytes(
            target,
            encryption_key_id=metadata.key_id,
            encryption_envelope_id="0" * 32,
            encrypted_data_key=metadata.encrypted_data_key,
            encrypted_data_key_nonce=metadata.encrypted_data_key_nonce,
        )
    assert target.is_file()


def test_v2_master_key_rotation_rewraps_without_rewriting_ciphertext(
    tmp_path,
    monkeypatch,
):
    configure_keys(monkeypatch)
    plaintext = b"passport and payment evidence"
    source = tmp_path / "source.bin"
    target = tmp_path / "document.dlcenc"
    source.write_bytes(plaintext)
    old_metadata = encrypt_file(source, target)
    ciphertext_before = target.read_bytes()

    monkeypatch.setattr(settings, "document_encryption_key_id", "documents-new")
    monkeypatch.setattr(settings, "document_encryption_key", DOC_NEW)
    monkeypatch.setattr(
        settings,
        "document_encryption_previous_keys",
        f"documents-old:{DOC_OLD}",
    )
    rotated = rotate_encrypted_file(
        target,
        **envelope_kwargs(old_metadata),
    )

    assert rotated.key_id == "documents-new"
    assert rotated.envelope_id == old_metadata.envelope_id
    assert rotated.encrypted_data_key != old_metadata.encrypted_data_key
    assert target.read_bytes() == ciphertext_before
    assert decrypt_file_bytes(target, **envelope_kwargs(rotated))[0] == plaintext

    monkeypatch.setattr(settings, "document_encryption_previous_keys", "")
    assert decrypt_file_bytes(target, **envelope_kwargs(rotated))[0] == plaintext
    with pytest.raises(DocumentEncryptionError, match="отсутствует"):
        decrypt_file_bytes(target, **envelope_kwargs(old_metadata))


def test_legacy_v1_remains_readable_for_controlled_migration(tmp_path, monkeypatch):
    configure_keys(monkeypatch)
    plaintext = b"legacy protected document"
    source = tmp_path / "source.bin"
    target = tmp_path / "legacy.dlcenc"
    source.write_bytes(plaintext)

    metadata = encrypt_file_legacy_v1(source, target)

    assert metadata.format_version == FORMAT_V1
    assert encrypted_format_version(target) == FORMAT_V1
    assert decrypt_file_bytes(target)[0] == plaintext


@pytest.mark.asyncio
async def test_plaintext_and_v1_documents_are_migrated_to_v2_and_audited(
    tmp_path,
    monkeypatch,
):
    configure_keys(monkeypatch)
    storage_root = tmp_path / "storage"
    case_dir = storage_root / "cases" / "7"
    case_dir.mkdir(parents=True)
    monkeypatch.setattr(settings, "storage_dir", str(storage_root))

    plaintext = b"%PDF-1.4\nlegacy plaintext document\n%%EOF\n"
    plaintext_path = case_dir / "legacy.pdf"
    plaintext_path.write_bytes(plaintext)
    plaintext_sha = hashlib.sha256(plaintext).hexdigest()

    v1_plaintext = b"%PDF-1.4\nlegacy encrypted document\n%%EOF\n"
    v1_source = tmp_path / "v1-source.pdf"
    v1_path = case_dir / "legacy-v1.dlcenc"
    v1_source.write_bytes(v1_plaintext)
    v1_metadata = encrypt_file_legacy_v1(v1_source, v1_path)
    v1_sha = hashlib.sha256(v1_plaintext).hexdigest()

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'documents.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    now = datetime.now(timezone.utc)
    async with factory() as db:
        db.add_all(
            [
                Document(
                    case_id=7,
                    uploaded_by_user_id=None,
                    document_type="DDU",
                    title="ДДУ",
                    file_name="legacy.pdf",
                    file_path=str(plaintext_path),
                    mime_type="application/pdf",
                    file_size=len(plaintext),
                    sha256=plaintext_sha,
                    detected_type="pdf",
                    security_status="VERIFIED",
                    scanned_at=now,
                    encryption_status=LEGACY_STATUS,
                    encryption_format_version=FORMAT_V1,
                    version=1,
                    status="UPLOADED",
                    is_required=False,
                ),
                Document(
                    case_id=8,
                    uploaded_by_user_id=None,
                    document_type="DDU",
                    title="ДДУ",
                    file_name="legacy-v1.pdf",
                    file_path=str(v1_path),
                    mime_type="application/pdf",
                    file_size=len(v1_plaintext),
                    sha256=v1_sha,
                    detected_type="pdf",
                    security_status="VERIFIED",
                    scanned_at=now,
                    encryption_status=ENCRYPTION_STATUS,
                    encryption_key_id=v1_metadata.key_id,
                    encryption_format_version=FORMAT_V1,
                    encrypted_at=v1_metadata.encrypted_at,
                    version=1,
                    status="UPLOADED",
                    is_required=False,
                ),
            ]
        )
        await db.commit()

    async with factory() as db:
        result = await migrate_document_encryption(db)
        await db.commit()
        first = await db.get(Document, 1)
        second = await db.get(Document, 2)
        integrity = await verify_audit_chain(db)

        assert result == {
            "encrypted": 1,
            "migrated_v1": 1,
            "rewrapped": 0,
            "repaired": 0,
            "failed": 0,
        }
        for document, expected in ((first, plaintext), (second, v1_plaintext)):
            assert document.encryption_status == ENCRYPTION_STATUS
            assert document.encryption_key_id == "documents-old"
            assert document.encryption_format_version == FORMAT_V2
            assert document.encryption_envelope_id
            assert document.encrypted_data_key
            assert document.encrypted_data_key_nonce
            assert document.encryption_error is None
            assert document.encrypted_at is not None
            assert encrypted_format_version(document.file_path) == FORMAT_V2
            assert (
                decrypt_file_bytes(
                    document.file_path,
                    expected_sha256=document.sha256,
                    encryption_key_id=document.encryption_key_id,
                    encryption_envelope_id=document.encryption_envelope_id,
                    encrypted_data_key=document.encrypted_data_key,
                    encrypted_data_key_nonce=document.encrypted_data_key_nonce,
                )[0]
                == expected
            )
        assert integrity["ok"] is True
        assert integrity["checked_count"] == 2

    await engine.dispose()
