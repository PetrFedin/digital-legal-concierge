from __future__ import annotations

from app.domain.cases.case_history import add_case_history_event
from app.domain.documents.document_service import (
    DocumentService,
    DuplicateDocumentError,
)
from app.domain.statuses.document_statuses import DocumentStatus
from app.models.case import Case
from app.security.document_access import DocumentActor


async def publish_self_filing_package(
    db,
    *,
    actor: DocumentActor,
    case: Case,
    stored,
):
    """Create one verified lawyer-authored package version and approve it.

    The storage pipeline has already validated/encrypted the bytes. Approval
    here means the authenticated responsible lawyer intentionally chose this
    exact SHA/document version as the client deliverable.
    """

    if actor.role != "lawyer" or actor.lawyer_id is None:
        raise ValueError("Итоговый пакет может утвердить только персональный юрист")
    document = await DocumentService(db).create_document(
        case=case,
        uploaded_by_user_id=None,
        document_type="SELF_FILING_PACKAGE",
        file_name=stored.original_name,
        file_path=stored.storage_path,
        mime_type=stored.mime_type,
        file_size=stored.file_size,
        sha256=stored.sha256,
        detected_type=stored.detected_type,
        security_status=stored.security_status,
        scanned_at=stored.scanned_at,
        encryption_status=stored.encryption_status,
        encryption_key_id=stored.encryption_key_id,
        encryption_format_version=stored.encryption_format_version,
        encryption_envelope_id=stored.encryption_envelope_id,
        encrypted_data_key=stored.encrypted_data_key,
        encrypted_data_key_nonce=stored.encrypted_data_key_nonce,
        encrypted_at=stored.encrypted_at,
        audit_actor_type="lawyer",
        audit_actor_id=int(actor.lawyer_id),
    )
    if document.status != DocumentStatus.UPLOADED:
        raise ValueError("Новая версия итогового пакета имеет неожиданный статус")
    document.status = DocumentStatus.APPROVED
    await add_case_history_event(
        db,
        actor_type="lawyer",
        actor_id=int(actor.lawyer_id),
        case_id=int(case.id),
        action="SELF_FILING_PACKAGE_DOCUMENT_APPROVED",
        new_value={
            "document_id": int(document.id),
            "version": int(document.version or 1),
            "sha256": document.sha256,
            "file_name": document.file_name,
            "security_status": document.security_status,
            "encryption_status": document.encryption_status,
        },
        comment="Юрист утвердил точную версию итогового пакета к выдаче клиенту",
    )
    await db.flush()
    return document


__all__ = [
    "DuplicateDocumentError",
    "publish_self_filing_package",
]
