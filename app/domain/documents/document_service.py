from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.statuses.document_statuses import DocumentStatus
from app.models.case import Case
from app.models.document import Document
from app.security.document_encryption import ENCRYPTION_STATUS, FORMAT_V2

DOC_TITLES = {
    "DDU": "ДДУ",
    "APPENDIX": "Приложение",
    "ADDITIONAL_AGREEMENT": "Допсоглашение",
    "TRANSFER_ACT": "Акт",
    "PAYMENT_PROOF": "Платежный документ",
    "CORRESPONDENCE": "Переписка",
    "POWER_OF_ATTORNEY": "Доверенность",
    "PASSPORT": "Паспорт / документ, удостоверяющий личность",
    "SELF_FILING_PACKAGE": "Пакет документов для самостоятельной подачи в суд",
    "OTHER": "Другой документ",
}

# A new upload supersedes an earlier version only after that earlier version
# entered the lawyer-review lifecycle. Multiple fresh uploads of the same type
# can still be assembled into one client package before submission.
SUPERSEDED_BY_NEW_UPLOAD = {
    DocumentStatus.ON_REVIEW,
    DocumentStatus.NEEDS_REUPLOAD,
    DocumentStatus.REJECTED,
}


class DuplicateDocumentError(ValueError):
    def __init__(self, document: Document):
        super().__init__(
            f"Этот файл уже загружен как «{document.title}», версия {document.version}."
        )
        self.document = document


class DocumentSecurityPendingError(ValueError):
    pass


class DocumentsAlreadySubmittedError(DocumentSecurityPendingError):
    """A retry reached the server after another request submitted the same drafts."""


class MissingRequiredDocumentsError(ValueError):
    def __init__(self, missing_types: list[str]):
        labels = [DOC_TITLES.get(item, item) for item in missing_types]
        super().__init__("Не хватает обязательных документов: " + ", ".join(labels))
        self.missing_types = tuple(missing_types)


def has_usable_document_envelope(document: Document) -> bool:
    return bool(
        document.encryption_status == ENCRYPTION_STATUS
        and int(document.encryption_format_version or 0) == FORMAT_V2
        and document.encryption_key_id
        and document.encryption_envelope_id
        and document.encrypted_data_key
        and document.encrypted_data_key_nonce
        and document.data_key_destroyed_at is None
        and document.encrypted_at
    )


def document_is_usable(document: Document) -> bool:
    return bool(
        document.security_status == "VERIFIED"
        and has_usable_document_envelope(document)
        and document.status
        not in {
            DocumentStatus.REJECTED,
            DocumentStatus.NEEDS_REUPLOAD,
            DocumentStatus.ARCHIVED,
        }
    )


def document_submission_reference(document: Document) -> str:
    return f"DOC-{int(document.id)}-V{int(document.version or 1)}"


class DocumentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_case_documents(self, case_id: int):
        result = await self.db.execute(
            select(Document)
            .where(Document.case_id == case_id)
            .order_by(Document.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_document(self, document_id: int):
        result = await self.db.execute(
            select(Document).where(Document.id == document_id)
        )
        return result.scalars().first()

    async def create_document(
        self,
        *,
        case,
        uploaded_by_user_id: int | None,
        document_type: str,
        file_name: str,
        file_path: str,
        mime_type: str | None,
        file_size: int | None,
        sha256: str | None = None,
        detected_type: str | None = None,
        security_status: str = "VERIFIED",
        scanned_at: datetime | None = None,
        encryption_status: str = ENCRYPTION_STATUS,
        encryption_key_id: str | None = None,
        encryption_format_version: int = FORMAT_V2,
        encryption_envelope_id: str | None = None,
        encrypted_data_key: str | None = None,
        encrypted_data_key_nonce: str | None = None,
        encrypted_at: datetime | None = None,
        audit_actor_type: str = "client",
        audit_actor_id: int | None = None,
    ):
        if security_status != "VERIFIED" or not sha256 or not scanned_at:
            raise ValueError("Документ не прошёл обязательную проверку безопасности")
        if (
            encryption_status != ENCRYPTION_STATUS
            or int(encryption_format_version or 0) != FORMAT_V2
            or not encryption_key_id
            or not encryption_envelope_id
            or not encrypted_data_key
            or not encrypted_data_key_nonce
            or not encrypted_at
        ):
            raise ValueError("Документ не прошёл обязательное envelope-шифрование")

        actor_type = str(audit_actor_type or "client").strip() or "client"
        actor_id = uploaded_by_user_id if audit_actor_id is None else int(audit_actor_id)

        # DB-backed production flows serialize version allocation on the case
        # row. Isolated domain tests may pass an already-authorized lightweight
        # case reference without persisting the Case model; those callers still
        # use the same document locks, duplicate checks and lifecycle rules.
        locked_case = (
            await self.db.execute(
                select(Case).where(Case.id == case.id).with_for_update()
            )
        ).scalar_one_or_none()
        case_id = int(locked_case.id if locked_case is not None else case.id)

        duplicate = (
            await self.db.execute(
                select(Document)
                .where(
                    Document.case_id == case_id,
                    Document.sha256 == sha256,
                    Document.security_status == "VERIFIED",
                )
                .order_by(Document.created_at.desc())
            )
        ).scalars().first()
        if duplicate:
            raise DuplicateDocumentError(duplicate)

        previous_versions = list(
            (
                await self.db.execute(
                    select(Document)
                    .where(
                        Document.case_id == case_id,
                        Document.document_type == document_type,
                    )
                    .order_by(Document.version.desc(), Document.id.desc())
                    .with_for_update()
                )
            ).scalars().all()
        )
        latest = previous_versions[0] if previous_versions else None
        version = int(latest.version or 0) + 1 if latest else 1
        document = Document(
            case_id=case_id,
            uploaded_by_user_id=uploaded_by_user_id,
            document_type=document_type,
            title=DOC_TITLES.get(document_type, "Документ"),
            file_name=file_name,
            file_path=file_path,
            mime_type=mime_type,
            file_size=file_size,
            sha256=sha256,
            detected_type=detected_type,
            security_status=security_status,
            scanned_at=scanned_at,
            encryption_status=encryption_status,
            encryption_key_id=encryption_key_id,
            encryption_format_version=encryption_format_version,
            encryption_envelope_id=encryption_envelope_id,
            encrypted_data_key=encrypted_data_key,
            encrypted_data_key_nonce=encrypted_data_key_nonce,
            encrypted_at=encrypted_at,
            version=version,
            status=DocumentStatus.UPLOADED,
        )
        self.db.add(document)
        await self.db.flush()

        superseded_old_values: list[dict[str, object]] = []
        superseded_ids: list[int] = []
        for previous in previous_versions:
            if previous.status not in SUPERSEDED_BY_NEW_UPLOAD:
                continue
            superseded_old_values.append(
                {
                    "document_id": previous.id,
                    "version": previous.version,
                    "status": str(previous.status),
                }
            )
            previous.status = DocumentStatus.ARCHIVED
            superseded_ids.append(int(previous.id))

        if superseded_ids:
            await add_case_history_event(
                self.db,
                actor_type=actor_type,
                actor_id=actor_id,
                case_id=case_id,
                action="DOCUMENT_PENDING_VERSION_SUPERSEDED",
                old_value={"documents": superseded_old_values},
                new_value={
                    "replacement_document_id": document.id,
                    "replacement_version": version,
                    "archived_document_ids": superseded_ids,
                },
                comment="Загружена новая версия документа вместо незавершённой",
            )

        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case_id,
            action="DOCUMENT_UPLOADED",
            new_value={
                "document_id": document.id,
                "type": document_type,
                "version": version,
                "sha256": sha256,
                "detected_type": detected_type,
                "size_bytes": file_size,
                "security_status": security_status,
                "encryption_status": encryption_status,
                "encryption_key_id": encryption_key_id,
                "encryption_format_version": encryption_format_version,
                "envelope_id_prefix": str(encryption_envelope_id)[:12],
                "superseded_document_ids": superseded_ids,
            },
        )
        return document

    async def record_rejected_upload(
        self,
        *,
        case_id: int,
        actor_id: int | None,
        document_type: str,
        file_name: str,
        reason_code: str,
        sha256: str | None,
    ) -> None:
        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=actor_id,
            case_id=case_id,
            action="DOCUMENT_UPLOAD_REJECTED",
            new_value={
                "type": document_type,
                "file_name": file_name,
                "reason_code": reason_code,
                "sha256": sha256,
            },
        )

    async def send_documents_to_review(
        self,
        *,
        case,
        actor_id: int,
        required_types: set[str] | None = None,
    ) -> int:
        locked_case = (
            await self.db.execute(
                select(Case).where(Case.id == case.id).with_for_update()
            )
        ).scalar_one_or_none()
        if locked_case is None:
            raise LookupError("Активное дело больше не найдено")

        documents = list(
            (
                await self.db.execute(
                    select(Document)
                    .where(Document.case_id == locked_case.id)
                    .order_by(Document.created_at.desc(), Document.id.desc())
                    .with_for_update()
                )
            ).scalars().all()
        )
        pending_security = [
            document
            for document in documents
            if document.status == DocumentStatus.UPLOADED
            and not document_is_usable(document)
        ]
        if pending_security:
            raise DocumentSecurityPendingError(
                "Часть документов ещё не прошла проверку или envelope-шифрование. "
                "Загрузите исправленные версии."
            )

        usable_types = {
            document.document_type
            for document in documents
            if document_is_usable(document)
        }
        missing = sorted(set(required_types or set()) - usable_types)
        if missing:
            raise MissingRequiredDocumentsError(missing)

        verified_new = [
            document
            for document in documents
            if document.status == DocumentStatus.UPLOADED
            and document_is_usable(document)
        ]
        if not verified_new:
            if any(
                document.status == DocumentStatus.ON_REVIEW
                for document in documents
            ):
                raise DocumentsAlreadySubmittedError(
                    "Все новые файлы уже переданы юристу. Обновите статусы документов."
                )
            if not documents:
                raise MissingRequiredDocumentsError(
                    sorted(required_types or {"DOCUMENT"})
                )
            raise DocumentsAlreadySubmittedError(
                "Новых файлов для передачи нет. Сначала добавьте документ."
            )

        submitted_at = datetime.now(timezone.utc)
        submitted_ids: list[int] = []
        submission_references: list[str] = []
        for document in verified_new:
            document.status = DocumentStatus.ON_REVIEW
            document.review_started_at = submitted_at
            # Keep TimestampMixin aligned with the explicit domain timestamp so
            # optimistic-write snapshots and audit views share one boundary.
            document.updated_at = submitted_at
            submitted_ids.append(int(document.id))
            submission_references.append(document_submission_reference(document))

        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=actor_id,
            case_id=locked_case.id,
            action="DOCUMENTS_SENT_TO_REVIEW",
            new_value={
                "new_count": len(verified_new),
                "document_ids": submitted_ids,
                "submission_references": submission_references,
                "submitted_at": submitted_at.isoformat(),
                "total_usable_count": sum(
                    1 for document in documents if document_is_usable(document)
                ),
                "required_types": sorted(required_types or set()),
            },
        )
        await self.db.flush()
        return len(verified_new)