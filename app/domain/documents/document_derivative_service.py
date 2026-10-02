from __future__ import annotations

import asyncio
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Protocol

import pikepdf
from pdfminer.high_level import extract_text
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.cases.case_history import add_case_history_event
from app.domain.documents.document_service import document_is_usable
from app.models.document import Document
from app.models.document_derivative import (
    DERIVATIVE_OCR_PDF,
    DERIVATIVE_READY,
    DERIVATIVE_SANITIZED_PDF,
    DocumentDerivative,
)
from app.security.document_encryption import (
    ENCRYPTION_STATUS,
    FORMAT_V2,
    DocumentEncryptionError,
    encrypt_bytes,
)
from app.storage import LocalStorageService


SANITIZE_RECIPE_ID = "dlc-int-01-pdf-sanitize-v1"


def _ocr_recipe_id() -> str:
    languages = str(settings.document_ocr_languages or "rus+eng").strip() or "rus+eng"
    return f"dlc-int-01-ocr-v1:{languages}:pdf:o0:skip-text"[:128]


class DocumentDerivativeError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)


@dataclass(frozen=True)
class PdfSanitizationResult:
    payload: bytes
    sha256: str
    page_count: int
    repair_applied: bool
    warnings_count: int
    has_usable_text: bool
    text_characters: int


@dataclass(frozen=True)
class OCRResult:
    payload: bytes
    sha256: str
    tool_version: str
    page_count: int
    has_usable_text: bool
    text_characters: int


@dataclass(frozen=True)
class DerivativeBuildResult:
    source_document_id: int
    sanitized: DocumentDerivative
    ocr: DocumentDerivative | None
    ocr_required: bool


class OCRExecutor(Protocol):
    @property
    def tool_version(self) -> str: ...

    def run(self, payload: bytes) -> OCRResult: ...


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _text_character_count(payload: bytes) -> int:
    try:
        text = extract_text(io.BytesIO(payload))
    except Exception as error:
        raise DocumentDerivativeError(
            "pdf_text_detection_failed",
            f"Не удалось проверить текстовый слой PDF: {type(error).__name__}",
        ) from error
    return sum(1 for character in text if not character.isspace())


def _has_usable_text(payload: bytes) -> tuple[bool, int]:
    count = _text_character_count(payload)
    threshold = max(1, int(settings.document_pdf_min_text_bytes))
    return count >= threshold, count


def _open_pdf(payload: bytes) -> tuple[pikepdf.Pdf, bool]:
    try:
        pdf = pikepdf.open(io.BytesIO(payload), attempt_recovery=False)
        return pdf, False
    except pikepdf.PasswordError as error:
        raise DocumentDerivativeError(
            "pdf_password_protected",
            "PDF защищён паролем и не может быть подготовлен как производный документ",
        ) from error
    except pikepdf.PdfError:
        try:
            pdf = pikepdf.open(io.BytesIO(payload), attempt_recovery=True)
            return pdf, True
        except pikepdf.PasswordError as error:
            raise DocumentDerivativeError(
                "pdf_password_protected",
                "PDF защищён паролем и не может быть подготовлен как производный документ",
            ) from error
        except pikepdf.PikepdfError as error:
            raise DocumentDerivativeError(
                "pdf_unrepairable",
                f"Структура PDF не подлежит безопасному восстановлению: {type(error).__name__}",
            ) from error


def sanitize_pdf_bytes(payload: bytes) -> PdfSanitizationResult:
    """Validate/repair a PDF and create a deterministic metadata-scrubbed derivative."""

    if not payload:
        raise DocumentDerivativeError("pdf_empty", "PDF пуст")
    pdf, repair_applied = _open_pdf(payload)
    try:
        if pdf.is_encrypted:
            raise DocumentDerivativeError(
                "pdf_encrypted",
                "Зашифрованный PDF не допускается в производный контур",
            )
        page_count = len(pdf.pages)
        if page_count <= 0:
            raise DocumentDerivativeError("pdf_no_pages", "PDF не содержит страниц")

        warnings_count = len(pdf.get_warnings())

        # The original evidence is never changed. Metadata cleanup is applied
        # only to this new derivative.
        if "/Metadata" in pdf.Root:
            del pdf.Root["/Metadata"]
        if "/Info" in pdf.trailer:
            del pdf.trailer["/Info"]

        output = io.BytesIO()
        pdf.save(
            output,
            encryption=False,
            deterministic_id=True,
            fix_metadata_version=False,
        )
        sanitized = output.getvalue()
    finally:
        pdf.close()

    # Re-open the produced derivative without recovery. A saved artifact is not
    # admitted merely because pikepdf managed to emit bytes.
    try:
        with pikepdf.open(io.BytesIO(sanitized), attempt_recovery=False) as checked:
            if checked.is_encrypted:
                raise DocumentDerivativeError(
                    "pdf_derivative_encrypted",
                    "Производный PDF неожиданно зашифрован внутри PDF-контейнера",
                )
            checked_page_count = len(checked.pages)
    except DocumentDerivativeError:
        raise
    except pikepdf.PikepdfError as error:
        raise DocumentDerivativeError(
            "pdf_derivative_invalid",
            f"Производный PDF не прошёл строгую проверку: {type(error).__name__}",
        ) from error

    if checked_page_count != page_count:
        raise DocumentDerivativeError(
            "pdf_page_count_changed",
            "Число страниц изменилось при безопасной подготовке PDF",
        )

    has_text, text_characters = _has_usable_text(sanitized)
    return PdfSanitizationResult(
        payload=sanitized,
        sha256=_sha256(sanitized),
        page_count=page_count,
        repair_applied=repair_applied,
        warnings_count=warnings_count,
        has_usable_text=has_text,
        text_characters=text_characters,
    )


class OCRmyPDFExecutor:
    @property
    def tool_version(self) -> str:
        try:
            return metadata.version("ocrmypdf")
        except metadata.PackageNotFoundError as error:
            raise DocumentDerivativeError(
                "ocrmypdf_unavailable",
                "OCRmyPDF не установлен в среде обработки",
            ) from error

    def run(self, payload: bytes) -> OCRResult:
        if not payload:
            raise DocumentDerivativeError("ocr_input_empty", "PDF для OCR пуст")

        timeout = max(30, min(int(settings.document_ocr_timeout_seconds), 1800))
        languages = str(settings.document_ocr_languages or "rus+eng").strip() or "rus+eng"
        processing_root = Path(settings.storage_dir).resolve() / ".processing"
        processing_root.mkdir(parents=True, exist_ok=True)
        try:
            processing_root.chmod(0o700)
        except OSError:
            pass

        with tempfile.TemporaryDirectory(prefix="dlc-ocr-", dir=processing_root) as directory:
            workdir = Path(directory)
            try:
                workdir.chmod(0o700)
            except OSError:
                pass
            source = workdir / "input.pdf"
            output = workdir / "output.pdf"
            with source.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                source.chmod(0o600)
            except OSError:
                pass

            command = [
                sys.executable,
                "-m",
                "ocrmypdf",
                "--output-type",
                "pdf",
                "--optimize",
                "0",
                "--skip-text",
                "--jobs",
                "1",
                "--language",
                languages,
                str(source),
                str(output),
            ]
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as error:
                raise DocumentDerivativeError(
                    "ocr_timeout",
                    "OCR превысил допустимое время обработки",
                ) from error
            if completed.returncode != 0:
                diagnostic = (completed.stderr or completed.stdout or "").strip()
                diagnostic = " ".join(diagnostic.split())[:180]
                raise DocumentDerivativeError(
                    "ocr_failed",
                    f"OCRmyPDF завершился с кодом {completed.returncode}: {diagnostic}",
                )
            if not output.is_file():
                raise DocumentDerivativeError(
                    "ocr_output_missing",
                    "OCRmyPDF не создал производный PDF",
                )
            ocr_payload = output.read_bytes()

        try:
            with pikepdf.open(io.BytesIO(ocr_payload), attempt_recovery=False) as pdf:
                if pdf.is_encrypted:
                    raise DocumentDerivativeError(
                        "ocr_output_encrypted",
                        "OCR-производный PDF неожиданно зашифрован внутри PDF-контейнера",
                    )
                page_count = len(pdf.pages)
        except DocumentDerivativeError:
            raise
        except pikepdf.PikepdfError as error:
            raise DocumentDerivativeError(
                "ocr_output_invalid",
                f"OCR-производный PDF повреждён: {type(error).__name__}",
            ) from error

        has_text, text_characters = _has_usable_text(ocr_payload)
        if not has_text:
            raise DocumentDerivativeError(
                "ocr_text_layer_missing",
                "OCR завершён, но пригодный текстовый слой не получен",
            )
        return OCRResult(
            payload=ocr_payload,
            sha256=_sha256(ocr_payload),
            tool_version=self.tool_version,
            page_count=page_count,
            has_usable_text=True,
            text_characters=text_characters,
        )


class DocumentDerivativeService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        storage: LocalStorageService | None = None,
        ocr_executor: OCRExecutor | None = None,
    ):
        self.db = db
        self.storage = storage or LocalStorageService()
        self.ocr_executor = ocr_executor or OCRmyPDFExecutor()

    async def _source_document(self, document_id: int) -> Document:
        document = (
            await self.db.execute(
                select(Document)
                .where(Document.id == int(document_id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not document:
            raise DocumentDerivativeError("source_not_found", "Исходный документ не найден")
        if not document_is_usable(document):
            raise DocumentDerivativeError(
                "source_not_usable",
                "Производный документ можно строить только из проверенного зашифрованного источника",
            )
        if str(document.detected_type or "").lower() != "pdf":
            raise DocumentDerivativeError(
                "source_not_pdf",
                "DLC-INT-01 обрабатывает только подтверждённые PDF",
            )
        if not document.sha256:
            raise DocumentDerivativeError(
                "source_sha_missing",
                "У исходного документа отсутствует SHA-256",
            )
        return document

    def _read_source(self, document: Document) -> bytes:
        return self.storage.read_document_bytes(
            document.file_path,
            expected_case_id=int(document.case_id),
            expected_sha256=document.sha256,
            encryption_key_id=document.encryption_key_id,
            encryption_envelope_id=document.encryption_envelope_id,
            encrypted_data_key=document.encrypted_data_key,
            encrypted_data_key_nonce=document.encrypted_data_key_nonce,
        )

    async def _identity_row(
        self,
        *,
        source_document_id: int,
        derivative_type: str,
        source_sha256: str,
        tool_name: str,
        tool_version: str,
        recipe_id: str,
    ) -> DocumentDerivative | None:
        return (
            await self.db.execute(
                select(DocumentDerivative)
                .where(
                    DocumentDerivative.source_document_id == source_document_id,
                    DocumentDerivative.derivative_type == derivative_type,
                    DocumentDerivative.source_sha256 == source_sha256,
                    DocumentDerivative.tool_name == tool_name,
                    DocumentDerivative.tool_version == tool_version,
                    DocumentDerivative.recipe_id == recipe_id,
                )
                .order_by(DocumentDerivative.id.desc())
            )
        ).scalars().first()

    async def _existing_ready(
        self,
        *,
        source_document_id: int,
        derivative_type: str,
        source_sha256: str,
        tool_name: str,
        tool_version: str,
        recipe_id: str,
    ) -> DocumentDerivative | None:
        row = await self._identity_row(
            source_document_id=source_document_id,
            derivative_type=derivative_type,
            source_sha256=source_sha256,
            tool_name=tool_name,
            tool_version=tool_version,
            recipe_id=recipe_id,
        )
        return row if row and row.status == DERIVATIVE_READY else None

    async def _record_failure(
        self,
        *,
        source: Document,
        derivative_type: str,
        tool_name: str,
        tool_version: str,
        recipe_id: str,
        error: DocumentDerivativeError,
        provenance: dict | None = None,
    ) -> DocumentDerivative:
        row = await self._identity_row(
            source_document_id=int(source.id),
            derivative_type=derivative_type,
            source_sha256=str(source.sha256),
            tool_name=tool_name,
            tool_version=tool_version,
            recipe_id=recipe_id,
        )
        if row and row.status == DERIVATIVE_READY:
            return row
        if row is None:
            row = DocumentDerivative(
                case_id=int(source.case_id),
                source_document_id=int(source.id),
                derivative_type=derivative_type,
                status="FAILED",
                source_sha256=str(source.sha256),
                tool_name=tool_name,
                tool_version=tool_version,
                recipe_id=recipe_id,
                provenance=provenance or {},
                encryption_status="PENDING",
            )
            self.db.add(row)
        else:
            row.status = "FAILED"
            row.provenance = provenance or row.provenance or {}
        row.error_code = error.code[:64]
        row.error_detail = str(error)[:255]
        await self.db.flush()
        await add_case_history_event(
            self.db,
            actor_type="system",
            actor_id=None,
            case_id=int(source.case_id),
            action="DOCUMENT_DERIVATIVE_FAILED",
            new_value={
                "source_document_id": int(source.id),
                "derivative_id": int(row.id),
                "derivative_type": derivative_type,
                "source_sha256": str(source.sha256),
                "tool": tool_name,
                "tool_version": tool_version,
                "recipe_id": recipe_id,
                "error_code": error.code,
            },
            comment="Производный документ не создан; исходный документ не изменён",
        )
        return row

    def _encrypt_derivative(
        self,
        *,
        case_id: int,
        payload: bytes,
        expected_sha256: str,
    ) -> tuple[str, object]:
        case_dir = self.storage.base_dir / "cases" / str(int(case_id))
        case_dir.mkdir(parents=True, exist_ok=True)
        try:
            case_dir.chmod(0o700)
        except OSError:
            pass
        target = case_dir / f"{uuid.uuid4().hex}.dlcenc"
        encryption = encrypt_bytes(
            payload,
            target,
            expected_sha256=expected_sha256,
        )
        try:
            target.chmod(0o600)
        except OSError:
            pass
        return (
            self.storage.storage_key_for_case_path(target, case_id=case_id),
            encryption,
        )

    async def _persist_ready(
        self,
        *,
        source: Document,
        derivative_type: str,
        payload: bytes,
        sha256: str,
        tool_name: str,
        tool_version: str,
        recipe_id: str,
        page_count: int,
        has_usable_text: bool,
        provenance: dict,
    ) -> DocumentDerivative:
        identity = await self._identity_row(
            source_document_id=int(source.id),
            derivative_type=derivative_type,
            source_sha256=str(source.sha256),
            tool_name=tool_name,
            tool_version=tool_version,
            recipe_id=recipe_id,
        )
        if identity and identity.status == DERIVATIVE_READY:
            return identity

        storage_key: str | None = None
        try:
            storage_key, encryption = await asyncio.to_thread(
                self._encrypt_derivative,
                case_id=int(source.case_id),
                payload=payload,
                expected_sha256=sha256,
            )
            derivative = identity or DocumentDerivative(
                case_id=int(source.case_id),
                source_document_id=int(source.id),
                derivative_type=derivative_type,
                source_sha256=str(source.sha256),
                tool_name=tool_name,
                tool_version=tool_version,
                recipe_id=recipe_id,
                provenance=provenance,
            )
            if identity is None:
                self.db.add(derivative)
            derivative.status = DERIVATIVE_READY
            derivative.file_path = storage_key
            derivative.mime_type = "application/pdf"
            derivative.file_size = len(payload)
            derivative.sha256 = sha256
            derivative.provenance = provenance
            derivative.page_count = page_count
            derivative.has_usable_text = has_usable_text
            derivative.encryption_status = ENCRYPTION_STATUS
            derivative.encryption_key_id = encryption.key_id
            derivative.encryption_format_version = encryption.format_version
            derivative.encryption_envelope_id = encryption.envelope_id
            derivative.encrypted_data_key = encryption.encrypted_data_key
            derivative.encrypted_data_key_nonce = encryption.encrypted_data_key_nonce
            derivative.data_key_destroyed_at = None
            derivative.encrypted_at = encryption.encrypted_at
            derivative.error_code = None
            derivative.error_detail = None
            await self.db.flush()
        except Exception:
            if storage_key:
                try:
                    self.storage.discard_stored_file(
                        storage_key,
                        expected_case_id=int(source.case_id),
                    )
                except Exception:
                    pass
            raise

        await add_case_history_event(
            self.db,
            actor_type="system",
            actor_id=None,
            case_id=int(source.case_id),
            action="DOCUMENT_DERIVATIVE_READY",
            new_value={
                "source_document_id": int(source.id),
                "derivative_id": int(derivative.id),
                "derivative_type": derivative_type,
                "source_sha256": str(source.sha256),
                "sha256": sha256,
                "tool": tool_name,
                "tool_version": tool_version,
                "recipe_id": recipe_id,
                "page_count": page_count,
                "has_usable_text": has_usable_text,
            },
        )
        return derivative

    async def ensure_pdf_derivatives(self, document_id: int) -> DerivativeBuildResult:
        source = await self._source_document(document_id)
        source_payload = await asyncio.to_thread(self._read_source, source)
        source_hash = _sha256(source_payload)
        if source_hash != str(source.sha256):
            raise DocumentEncryptionError("Исходный документ не соответствует SHA-256 записи")

        pike_version = str(getattr(pikepdf, "__version__", metadata.version("pikepdf")))
        existing_sanitized = await self._existing_ready(
            source_document_id=int(source.id),
            derivative_type=DERIVATIVE_SANITIZED_PDF,
            source_sha256=str(source.sha256),
            tool_name="pikepdf",
            tool_version=pike_version,
            recipe_id=SANITIZE_RECIPE_ID,
        )
        if existing_sanitized:
            sanitized = existing_sanitized
        else:
            try:
                result = await asyncio.to_thread(sanitize_pdf_bytes, source_payload)
            except DocumentDerivativeError as error:
                await self._record_failure(
                    source=source,
                    derivative_type=DERIVATIVE_SANITIZED_PDF,
                    tool_name="pikepdf",
                    tool_version=pike_version,
                    recipe_id=SANITIZE_RECIPE_ID,
                    error=error,
                    provenance={
                        "source_document_id": int(source.id),
                        "source_sha256": str(source.sha256),
                    },
                )
                raise
            sanitized = await self._persist_ready(
                source=source,
                derivative_type=DERIVATIVE_SANITIZED_PDF,
                payload=result.payload,
                sha256=result.sha256,
                tool_name="pikepdf",
                tool_version=pike_version,
                recipe_id=SANITIZE_RECIPE_ID,
                page_count=result.page_count,
                has_usable_text=result.has_usable_text,
                provenance={
                    "source_document_id": int(source.id),
                    "source_sha256": str(source.sha256),
                    "repair_applied": result.repair_applied,
                    "warnings_count": result.warnings_count,
                    "metadata_scrubbed": True,
                    "text_characters": result.text_characters,
                },
            )

        if bool(sanitized.has_usable_text):
            return DerivativeBuildResult(
                source_document_id=int(source.id),
                sanitized=sanitized,
                ocr=None,
                ocr_required=False,
            )

        try:
            ocr_version = self.ocr_executor.tool_version
        except DocumentDerivativeError as error:
            await self._record_failure(
                source=source,
                derivative_type=DERIVATIVE_OCR_PDF,
                tool_name="ocrmypdf",
                tool_version="unavailable",
                recipe_id=_ocr_recipe_id(),
                error=error,
                provenance={
                    "source_document_id": int(source.id),
                    "source_sha256": str(source.sha256),
                    "input_derivative_id": int(sanitized.id),
                },
            )
            raise
        existing_ocr = await self._existing_ready(
            source_document_id=int(source.id),
            derivative_type=DERIVATIVE_OCR_PDF,
            source_sha256=str(source.sha256),
            tool_name="ocrmypdf",
            tool_version=ocr_version,
            recipe_id=_ocr_recipe_id(),
        )
        if existing_ocr:
            return DerivativeBuildResult(
                source_document_id=int(source.id),
                sanitized=sanitized,
                ocr=existing_ocr,
                ocr_required=True,
            )

        if not sanitized.file_path:
            raise DocumentDerivativeError(
                "sanitized_storage_missing",
                "У подготовленного PDF отсутствует защищённое хранилище",
            )
        sanitized_payload = await asyncio.to_thread(
            self.storage.read_document_bytes,
            sanitized.file_path,
            expected_case_id=int(source.case_id),
            expected_sha256=sanitized.sha256,
            encryption_key_id=sanitized.encryption_key_id,
            encryption_envelope_id=sanitized.encryption_envelope_id,
            encrypted_data_key=sanitized.encrypted_data_key,
            encrypted_data_key_nonce=sanitized.encrypted_data_key_nonce,
        )
        try:
            ocr_result = await asyncio.to_thread(
                self.ocr_executor.run,
                sanitized_payload,
            )
            if ocr_result.page_count != int(sanitized.page_count or 0):
                raise DocumentDerivativeError(
                    "ocr_page_count_changed",
                    "Число страниц изменилось при OCR",
                )
        except DocumentDerivativeError as error:
            await self._record_failure(
                source=source,
                derivative_type=DERIVATIVE_OCR_PDF,
                tool_name="ocrmypdf",
                tool_version=ocr_version,
                recipe_id=_ocr_recipe_id(),
                error=error,
                provenance={
                    "source_document_id": int(source.id),
                    "source_sha256": str(source.sha256),
                    "input_derivative_id": int(sanitized.id),
                    "input_sha256": str(sanitized.sha256),
                    "languages": str(settings.document_ocr_languages),
                },
            )
            raise
        ocr = await self._persist_ready(
            source=source,
            derivative_type=DERIVATIVE_OCR_PDF,
            payload=ocr_result.payload,
            sha256=ocr_result.sha256,
            tool_name="ocrmypdf",
            tool_version=ocr_result.tool_version,
            recipe_id=_ocr_recipe_id(),
            page_count=ocr_result.page_count,
            has_usable_text=ocr_result.has_usable_text,
            provenance={
                "source_document_id": int(source.id),
                "source_sha256": str(source.sha256),
                "input_derivative_id": int(sanitized.id),
                "input_sha256": str(sanitized.sha256),
                "languages": str(settings.document_ocr_languages),
                "output_type": "pdf",
                "optimize": 0,
                "text_characters": ocr_result.text_characters,
            },
        )
        return DerivativeBuildResult(
            source_document_id=int(source.id),
            sanitized=sanitized,
            ocr=ocr,
            ocr_required=True,
        )

    async def _needs_pdf_derivative_work(self, document_id: int) -> bool:
        source = await self.db.get(Document, int(document_id))
        if not source or not source.sha256:
            return False
        pike_version = str(getattr(pikepdf, "__version__", metadata.version("pikepdf")))
        sanitized = await self._existing_ready(
            source_document_id=int(source.id),
            derivative_type=DERIVATIVE_SANITIZED_PDF,
            source_sha256=str(source.sha256),
            tool_name="pikepdf",
            tool_version=pike_version,
            recipe_id=SANITIZE_RECIPE_ID,
        )
        if sanitized is None:
            return True
        if bool(sanitized.has_usable_text):
            return False
        try:
            ocr_version = self.ocr_executor.tool_version
        except DocumentDerivativeError:
            return True
        ocr = await self._existing_ready(
            source_document_id=int(source.id),
            derivative_type=DERIVATIVE_OCR_PDF,
            source_sha256=str(source.sha256),
            tool_name="ocrmypdf",
            tool_version=ocr_version,
            recipe_id=_ocr_recipe_id(),
        )
        return ocr is None

    async def build_missing_pdf_derivatives(
        self,
        *,
        limit: int = 10,
    ) -> dict[str, int]:
        """Build a bounded batch; the scheduler owns retry cadence and commit."""

        candidate_ids = list(
            (
                await self.db.execute(
                    select(Document.id)
                    .where(
                        Document.security_status == "VERIFIED",
                        Document.detected_type == "pdf",
                        Document.encryption_status == ENCRYPTION_STATUS,
                        Document.data_key_destroyed_at.is_(None),
                    )
                    .order_by(Document.id.asc())
                    .limit(max(10, min(int(limit) * 20, 1000)))
                )
            ).scalars().all()
        )
        result = {
            "processed": 0,
            "searchable_without_ocr": 0,
            "ocr_created": 0,
            "failed": 0,
        }
        max_items = max(1, min(int(limit), 100))
        for document_id in candidate_ids:
            if result["processed"] + result["failed"] >= max_items:
                break
            if not await self._needs_pdf_derivative_work(int(document_id)):
                continue
            try:
                built = await self.ensure_pdf_derivatives(int(document_id))
            except (DocumentDerivativeError, DocumentEncryptionError, OSError, ValueError):
                result["failed"] += 1
                continue
            result["processed"] += 1
            if built.ocr_required and built.ocr is not None:
                result["ocr_created"] += 1
            elif not built.ocr_required:
                result["searchable_without_ocr"] += 1
        return result


__all__ = [
    "DerivativeBuildResult",
    "DocumentDerivativeError",
    "DocumentDerivativeService",
    "OCRExecutor",
    "OCRResult",
    "OCRmyPDFExecutor",
    "PdfSanitizationResult",
    "sanitize_pdf_bytes",
]
