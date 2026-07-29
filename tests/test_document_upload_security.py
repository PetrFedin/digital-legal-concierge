from __future__ import annotations

import hashlib
import io
import os
import struct
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.domain.documents.document_service import (
    DocumentService,
    DuplicateDocumentError,
)
from app.models import Base
from app.security.file_uploads import (
    UploadSecurityError,
    cleanup_quarantine,
    inspect_upload,
    preflight_upload,
    safe_filename,
)
from app.storage import LocalStorageService


def valid_pdf(*, extra: bytes = b"") -> bytes:
    return b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n" + extra + b"\n%%EOF\n"


def valid_png(width: int = 2, height: int = 3) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)
    ihdr += b"\x08\x02\x00\x00\x00" + b"\x00\x00\x00\x00"
    iend = struct.pack(">I", 0) + b"IEND" + b"\x00\x00\x00\x00"
    return signature + ihdr + iend


def valid_jpeg(width: int = 2, height: int = 3) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof_payload = (
        b"\x08"
        + struct.pack(">HH", height, width)
        + b"\x03"
        + b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    )
    sof0 = b"\xff\xc0" + struct.pack(">H", len(sof_payload) + 2) + sof_payload
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def docx_bytes(*, macro: bool = False, unsafe_member: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        content_type = (
            "application/vnd.ms-word.document.macroEnabled.main+xml"
            if macro
            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
        )
        archive.writestr(
            "[Content_Types].xml",
            f'<Types><Override ContentType="{content_type}"/></Types>',
        )
        archive.writestr("_rels/.rels", "<Relationships/>")
        archive.writestr("word/document.xml", "<w:document/>")
        if macro:
            archive.writestr("word/vbaProject.bin", b"macro")
        if unsafe_member:
            archive.writestr(unsafe_member, b"unsafe")
    return buffer.getvalue()


class FakeBot:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.get_file_calls = 0
        self.download_calls = 0

    async def get_file(self, telegram_file_id: str):
        self.get_file_calls += 1
        return SimpleNamespace(file_path=f"telegram/{telegram_file_id}")

    async def download_file(self, file_path: str, destination: Path):
        self.download_calls += 1
        Path(destination).write_bytes(self.payload)


def inspect_bytes(
    tmp_path: Path,
    payload: bytes,
    *,
    name: str,
    mime: str,
):
    path = tmp_path / "candidate"
    path.write_bytes(payload)
    return inspect_upload(
        path,
        original_name=name,
        claimed_mime=mime,
        declared_size=len(payload),
        max_bytes=20 * 1024 * 1024,
    )


def test_safe_filename_removes_paths_controls_and_reserved_names():
    assert safe_filename("../../ДДУ\x00 финал.PDF") == "ДДУ финал.pdf"
    assert safe_filename("C:\\temp\\CON.pdf") == "file_CON.pdf"
    assert "/" not in safe_filename("../../passport.jpg")


def test_preflight_rejects_size_before_telegram_download():
    with pytest.raises(UploadSecurityError) as error:
        preflight_upload(
            original_name="contract.pdf",
            claimed_mime="application/pdf",
            declared_size=21 * 1024 * 1024,
            max_bytes=20 * 1024 * 1024,
        )
    assert error.value.code == "file_too_large"


@pytest.mark.parametrize(
    ("name", "mime", "payload", "detected"),
    [
        ("contract.pdf", "application/pdf", valid_pdf(), "pdf"),
        ("scan.png", "image/png", valid_png(), "png"),
        ("scan.jpg", "image/jpeg", valid_jpeg(), "jpeg"),
        (
            "contract.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            docx_bytes(),
            "docx",
        ),
    ],
)
def test_allowed_formats_are_identified_from_content(
    tmp_path,
    name,
    mime,
    payload,
    detected,
):
    inspection = inspect_bytes(tmp_path, payload, name=name, mime=mime)
    assert inspection.detected_type == detected
    assert inspection.sha256 == hashlib.sha256(payload).hexdigest()
    assert inspection.size_bytes == len(payload)


def test_extension_magic_mismatch_is_rejected(tmp_path):
    with pytest.raises(UploadSecurityError) as error:
        inspect_bytes(
            tmp_path,
            valid_png(),
            name="renamed.pdf",
            mime="application/pdf",
        )
    assert error.value.code == "content_type_mismatch"
    assert error.value.sha256


@pytest.mark.parametrize(
    ("payload", "name", "mime", "code"),
    [
        (
            valid_pdf(extra=b"<</OpenAction<</S/JavaScript/JS(app.alert())>>>>"),
            "active.pdf",
            "application/pdf",
            "active_pdf_content",
        ),
        (
            valid_pdf(extra=b"<</Encrypt 3 0 R>>"),
            "password.pdf",
            "application/pdf",
            "encrypted_pdf",
        ),
        (
            docx_bytes(macro=True),
            "macro.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "active_docx_content",
        ),
        (
            docx_bytes(unsafe_member="../payload.exe"),
            "unsafe.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "unsafe_archive_path",
        ),
    ],
)
def test_active_or_unsafe_content_is_rejected(tmp_path, payload, name, mime, code):
    with pytest.raises(UploadSecurityError) as error:
        inspect_bytes(tmp_path, payload, name=name, mime=mime)
    assert error.value.code == code


@pytest.mark.asyncio
async def test_storage_uses_digest_key_and_never_user_path(tmp_path, monkeypatch):
    payload = valid_pdf()
    bot = FakeBot(payload)
    monkeypatch.setattr(settings, "max_document_upload_mb", 20)
    monkeypatch.setattr(settings, "quarantine_rejected_uploads", True)
    storage = LocalStorageService(str(tmp_path / "storage"))

    stored = await storage.save_telegram_file(
        bot=bot,
        telegram_file_id="file-1",
        case_id=42,
        original_name="../../ДДУ финал.pdf",
        mime_type="application/pdf",
        file_size=len(payload),
    )

    expected_hash = hashlib.sha256(payload).hexdigest()
    assert stored.sha256 == expected_hash
    assert stored.security_status == "VERIFIED"
    assert Path(stored.storage_path).name == f"{expected_hash}.pdf"
    assert Path(stored.storage_path).read_bytes() == payload
    assert "ДДУ" not in Path(stored.storage_path).name
    assert not any((tmp_path / "storage" / ".incoming").iterdir())


@pytest.mark.asyncio
async def test_rejected_download_is_quarantined_without_case_file(tmp_path, monkeypatch):
    payload = valid_pdf(extra=b"<</Launch<</F(evil.exe)>>>>")
    bot = FakeBot(payload)
    monkeypatch.setattr(settings, "max_document_upload_mb", 20)
    monkeypatch.setattr(settings, "quarantine_rejected_uploads", True)
    storage_root = tmp_path / "storage"

    with pytest.raises(UploadSecurityError) as error:
        await LocalStorageService(str(storage_root)).save_telegram_file(
            bot=bot,
            telegram_file_id="file-2",
            case_id=7,
            original_name="contract.pdf",
            mime_type="application/pdf",
            file_size=len(payload),
        )

    assert error.value.code == "active_pdf_content"
    assert error.value.quarantine_path
    assert Path(error.value.quarantine_path).is_file()
    assert not (storage_root / "cases" / "7").exists()
    assert list((storage_root / "quarantine").glob("*.json"))


@pytest.mark.asyncio
async def test_oversized_declared_file_is_rejected_without_network(tmp_path, monkeypatch):
    bot = FakeBot(valid_pdf())
    monkeypatch.setattr(settings, "max_document_upload_mb", 1)
    with pytest.raises(UploadSecurityError) as error:
        await LocalStorageService(str(tmp_path)).save_telegram_file(
            bot=bot,
            telegram_file_id="large",
            case_id=1,
            original_name="large.pdf",
            mime_type="application/pdf",
            file_size=2 * 1024 * 1024,
        )
    assert error.value.code == "file_too_large"
    assert bot.get_file_calls == 0
    assert bot.download_calls == 0


@pytest.mark.asyncio
async def test_duplicate_sha_is_rejected_in_same_case(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'documents.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    scanned_at = datetime.now(timezone.utc)
    case = SimpleNamespace(id=1)
    async with factory() as db:
        service = DocumentService(db)
        document = await service.create_document(
            case=case,
            uploaded_by_user_id=None,
            document_type="DDU",
            file_name="contract.pdf",
            file_path="/safe/hash.pdf",
            mime_type="application/pdf",
            file_size=100,
            sha256="a" * 64,
            detected_type="pdf",
            security_status="VERIFIED",
            scanned_at=scanned_at,
        )
        assert document.id
        with pytest.raises(DuplicateDocumentError):
            await service.create_document(
                case=case,
                uploaded_by_user_id=None,
                document_type="OTHER",
                file_name="copy.pdf",
                file_path="/safe/hash.pdf",
                mime_type="application/pdf",
                file_size=100,
                sha256="a" * 64,
                detected_type="pdf",
                security_status="VERIFIED",
                scanned_at=scanned_at,
            )
    await engine.dispose()


def test_quarantine_cleanup_removes_only_expired_files(tmp_path):
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    old = quarantine / "old.quarantine"
    recent = quarantine / "recent.quarantine"
    old.write_bytes(b"old")
    recent.write_bytes(b"recent")
    old_timestamp = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(old, (old_timestamp, old_timestamp))

    assert cleanup_quarantine(tmp_path, retention_days=7) == 1
    assert not old.exists()
    assert recent.exists()
