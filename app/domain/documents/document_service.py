from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.statuses.document_statuses import DocumentStatus
from app.models.document import Document

DOC_TITLES = {
    "DDU": "ДДУ",
    "APPENDIX": "Приложение",
    "ADDITIONAL_AGREEMENT": "Допсоглашение",
    "TRANSFER_ACT": "Акт",
    "PAYMENT_PROOF": "Платежный документ",
    "CORRESPONDENCE": "Переписка",
    "OTHER": "Другой документ",
}


class DuplicateDocumentError(ValueError):
    def __init__(self, document: Document):
        super().__init__(
            f"Этот файл уже загружен как «{document.title}», версия {document.version}."
        )
        self.document = document


class DocumentSecurityPendingError(ValueError):
    pass


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
    ):
        if security_status != "VERIFIED" or not sha256 or not scanned_at:
            raise ValueError("Документ не прошёл обязательную проверку безопасности")

        duplicate = (
            await self.db.execute(
                select(Document)
                .where(
                    Document.case_id == case.id,
                    Document.sha256 == sha256,
                    Document.security_status == "VERIFIED",
                )
                .order_by(Document.created_at.desc())
            )
        ).scalars().first()
        if duplicate:
            raise DuplicateDocumentError(duplicate)

        latest = (
            await self.db.execute(
                select(Document)
                .where(
                    Document.case_id == case.id,
                    Document.document_type == document_type,
                )
                .order_by(Document.version.desc())
            )
        ).scalars().first()
        version = latest.version + 1 if latest else 1
        document = Document(
            case_id=case.id,
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
            version=version,
            status=DocumentStatus.UPLOADED,
        )
        self.db.add(document)
        await self.db.flush()
        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=uploaded_by_user_id,
            case_id=case.id,
            action="DOCUMENT_UPLOADED",
            new_value={
                "document_id": document.id,
                "type": document_type,
                "version": version,
                "sha256": sha256,
                "detected_type": detected_type,
                "size_bytes": file_size,
                "security_status": security_status,
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

    async def send_documents_to_review(self, *, case, actor_id: int):
        documents = await self.list_case_documents(case.id)
        pending = [
            document
            for document in documents
            if document.status == DocumentStatus.UPLOADED
            and document.security_status != "VERIFIED"
        ]
        if pending:
            raise DocumentSecurityPendingError(
                "Часть документов ещё не прошла проверку безопасности. "
                "Удалите их и загрузите заново либо дождитесь повторной проверки."
            )
        verified = [
            document
            for document in documents
            if document.status == DocumentStatus.UPLOADED
            and document.security_status == "VERIFIED"
        ]
        for document in verified:
            document.status = DocumentStatus.ON_REVIEW
        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=actor_id,
            case_id=case.id,
            action="DOCUMENTS_SENT_TO_REVIEW",
            new_value={"count": len(verified)},
        )
        await self.db.flush()
