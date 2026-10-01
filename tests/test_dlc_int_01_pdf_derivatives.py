from __future__ import annotations

import hashlib
import io
import re
from datetime import datetime, timezone
from pathlib import Path

import pikepdf
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.domain.documents.document_derivative_service import (
    DocumentDerivativeError,
    DocumentDerivativeService,
    OCRResult,
    sanitize_pdf_bytes,
)
from app.models import Base
from app.models.case import Case
from app.models.document import Document
from app.models.document_derivative import (
    DERIVATIVE_OCR_PDF,
    DERIVATIVE_READY,
    DERIVATIVE_SANITIZED_PDF,
    DocumentDerivative,
)
from app.models.user import User
from app.security.document_encryption import (
    ENCRYPTION_STATUS,
    FORMAT_V2,
    encrypt_bytes,
    is_encrypted_file,
)
from app.storage import LocalStorageService


DOCUMENT_KEY = "dlc-int-01-document-key-" + "x" * 40
AUDIT_KEY = "dlc-int-01-audit-key-" + "y" * 40


def pdf_bytes(*, text: str | None = None) -> bytes:
    stream = (
        f"BT /F1 12 Tf 36 120 Td ({text}) Tj ET".encode("ascii")
        if text
        else b""
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 180] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    payload = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode("ascii"))
        payload.extend(obj)
        payload.extend(b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects)+1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(payload)


def configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "max_document_upload_mb", 20)
    monkeypatch.setattr(settings, "document_pdf_min_text_bytes", 12)
    monkeypatch.setattr(settings, "document_ocr_languages", "eng")
    monkeypatch.setattr(settings, "document_ocr_timeout_seconds", 60)
    monkeypatch.setattr(settings, "document_encryption_key_id", "documents-int-01")
    monkeypatch.setattr(settings, "document_encryption_key", DOCUMENT_KEY)
    monkeypatch.setattr(settings, "document_encryption_previous_keys", "")
    monkeypatch.setattr(settings, "allow_legacy_security_key_fallback", False)
    monkeypatch.setattr(settings, "audit_integrity_key_id", "audit-int-01")
    monkeypatch.setattr(settings, "audit_integrity_key", AUDIT_KEY)
    monkeypatch.setattr(settings, "audit_integrity_previous_keys", "")


def test_pikepdf_keeps_source_immutable_and_creates_valid_searchable_derivative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(monkeypatch, tmp_path)
    source = pdf_bytes(text="Searchable legal evidence text layer")
    source_sha = hashlib.sha256(source).hexdigest()

    result = sanitize_pdf_bytes(source)

    assert hashlib.sha256(source).hexdigest() == source_sha
    assert result.sha256 != ""
    assert result.page_count == 1
    assert result.has_usable_text is True
    assert result.text_characters >= 12
    with pikepdf.open(io.BytesIO(result.payload), attempt_recovery=False) as checked:
        assert len(checked.pages) == 1
        assert checked.is_encrypted is False


def test_pikepdf_repairs_bad_startxref_without_overwriting_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(monkeypatch, tmp_path)
    source = pdf_bytes(text="Repairable legal PDF")
    damaged = re.sub(
        rb"startxref\n\d+\n%%EOF",
        b"startxref\n0\n%%EOF",
        source,
    )
    before = hashlib.sha256(damaged).hexdigest()

    result = sanitize_pdf_bytes(damaged)

    assert hashlib.sha256(damaged).hexdigest() == before
    assert result.repair_applied is True
    assert result.page_count == 1


def test_pikepdf_rejects_password_protected_pdf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(monkeypatch, tmp_path)
    source = pdf_bytes(text="Protected")
    output = io.BytesIO()
    with pikepdf.open(io.BytesIO(source)) as pdf:
        pdf.save(
            output,
            encryption=pikepdf.Encryption(
                owner="owner-secret",
                user="user-secret",
                R=6,
            ),
        )

    with pytest.raises(DocumentDerivativeError) as error:
        sanitize_pdf_bytes(output.getvalue())

    assert error.value.code == "pdf_password_protected"


class ForbiddenOCR:
    @property
    def tool_version(self) -> str:
        raise AssertionError("OCR must not be consulted for searchable PDF")

    def run(self, payload: bytes) -> OCRResult:
        raise AssertionError("OCR must not run for searchable PDF")


class FakeOCR:
    tool_version = "fake-ocr-1"

    def run(self, payload: bytes) -> OCRResult:
        output = pdf_bytes(text="Recognized OCR legal evidence text layer")
        return OCRResult(
            payload=output,
            sha256=hashlib.sha256(output).hexdigest(),
            tool_version=self.tool_version,
            page_count=1,
            has_usable_text=True,
            text_characters=36,
        )


async def seed_source(
    db,
    *,
    storage: LocalStorageService,
    payload: bytes,
    suffix: int,
) -> tuple[Case, Document, bytes]:
    user = User(
        telegram_id=910000 + suffix,
        full_name=f"Derivative client {suffix}",
    )
    db.add(user)
    await db.flush()
    case = Case(
        case_number=f"DER-{suffix:04d}",
        client_id=user.id,
        route="M1",
        status="M1_DOCUMENTS_COLLECTION",
    )
    db.add(case)
    await db.flush()

    sha = hashlib.sha256(payload).hexdigest()
    target = storage.base_dir / "cases" / str(case.id) / f"{suffix:032x}.dlcenc"
    target.parent.mkdir(parents=True, exist_ok=True)
    encryption = encrypt_bytes(payload, target, expected_sha256=sha)
    document = Document(
        case_id=case.id,
        uploaded_by_user_id=user.id,
        document_type="DDU",
        title="ДДУ",
        file_name="evidence.pdf",
        file_path=storage.storage_key_for_case_path(target, case_id=case.id),
        mime_type="application/pdf",
        file_size=len(payload),
        sha256=sha,
        detected_type="pdf",
        security_status="VERIFIED",
        security_reason="malware=CLEAN;engine=test",
        scanned_at=datetime.now(timezone.utc),
        encryption_status=ENCRYPTION_STATUS,
        encryption_key_id=encryption.key_id,
        encryption_format_version=FORMAT_V2,
        encryption_envelope_id=encryption.envelope_id,
        encrypted_data_key=encryption.encrypted_data_key,
        encrypted_data_key_nonce=encryption.encrypted_data_key_nonce,
        encrypted_at=encryption.encrypted_at,
        version=1,
        status="UPLOADED",
    )
    db.add(document)
    await db.flush()
    return case, document, target.read_bytes()


@pytest.mark.asyncio
async def test_searchable_source_creates_one_encrypted_sanitized_derivative_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(monkeypatch, tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'searchable.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    storage = LocalStorageService()
    async with factory() as db:
        _, source, source_ciphertext = await seed_source(
            db,
            storage=storage,
            payload=pdf_bytes(text="Searchable contract legal evidence text"),
            suffix=1,
        )
        source_sha = source.sha256
        result = await DocumentDerivativeService(
            db,
            storage=storage,
            ocr_executor=ForbiddenOCR(),
        ).ensure_pdf_derivatives(source.id)
        await db.commit()

        assert result.ocr_required is False
        assert result.ocr is None
        assert result.sanitized.status == DERIVATIVE_READY
        assert result.sanitized.derivative_type == DERIVATIVE_SANITIZED_PDF
        assert result.sanitized.source_sha256 == source_sha
        assert result.sanitized.sha256
        derivative_path = storage.resolve_storage_path(
            result.sanitized.file_path,
            expected_case_id=source.case_id,
        )
        assert is_encrypted_file(derivative_path)
        assert derivative_path.read_bytes() != source_ciphertext

        await db.refresh(source)
        source_path = storage.resolve_storage_path(
            source.file_path,
            expected_case_id=source.case_id,
        )
        assert source.sha256 == source_sha
        assert source_path.read_bytes() == source_ciphertext

        repeated = await DocumentDerivativeService(
            db,
            storage=storage,
            ocr_executor=ForbiddenOCR(),
        ).ensure_pdf_derivatives(source.id)
        assert repeated.sanitized.id == result.sanitized.id
        assert (
            await db.execute(select(func.count(DocumentDerivative.id)))
        ).scalar_one() == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_scan_only_source_creates_separate_ocr_derivative_with_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(monkeypatch, tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ocr.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    storage = LocalStorageService()
    async with factory() as db:
        _, source, source_ciphertext = await seed_source(
            db,
            storage=storage,
            payload=pdf_bytes(),
            suffix=2,
        )
        result = await DocumentDerivativeService(
            db,
            storage=storage,
            ocr_executor=FakeOCR(),
        ).ensure_pdf_derivatives(source.id)
        await db.commit()

        assert result.ocr_required is True
        assert result.sanitized.has_usable_text is False
        assert result.ocr is not None
        assert result.ocr.derivative_type == DERIVATIVE_OCR_PDF
        assert result.ocr.has_usable_text is True
        assert result.ocr.provenance["input_derivative_id"] == result.sanitized.id
        assert result.ocr.source_document_id == source.id
        assert result.ocr.source_sha256 == source.sha256

        rows = list(
            (
                await db.execute(
                    select(DocumentDerivative).order_by(DocumentDerivative.id)
                )
            ).scalars().all()
        )
        assert [row.derivative_type for row in rows] == [
            DERIVATIVE_SANITIZED_PDF,
            DERIVATIVE_OCR_PDF,
        ]
        for row in rows:
            path = storage.resolve_storage_path(
                row.file_path,
                expected_case_id=source.case_id,
            )
            assert is_encrypted_file(path)

        source_path = storage.resolve_storage_path(
            source.file_path,
            expected_case_id=source.case_id,
        )
        assert source_path.read_bytes() == source_ciphertext

    await engine.dispose()
