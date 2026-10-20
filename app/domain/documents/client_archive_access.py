"""Fail-closed metadata preflight for future client archive document delivery.

This service never opens/decrypts/sends the file and is NOT a download grant.
Real delivery must reauthorize immediately before consumption and separately
enforce channel consent, one-use credentials, audit and legal approval.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.documents.document_service import has_usable_document_envelope
from app.domain.statuses.case_statuses import CaseStatus
from app.models.case import Case
from app.models.case_retention import CaseRetentionRecord
from app.models.document import Document
from app.models.user import User

# Once erasure is approved, archived content must be unavailable even while
# file deletion and the retained financial/audit ledger complete asynchronously.
RESTRICTED_RETENTION_STATES = frozenset(
    {"APPROVED", "EXECUTING", "FAILED", "COMPLETED"}
)
TERMINAL_CASE_STATES = (
    CaseStatus.M1_CLOSED,
    CaseStatus.M2_CLOSED,
    CaseStatus.ARCHIVED,
)


class ClientArchiveDocumentDenied(ValueError):
    """An indistinguishable denial for missing, foreign or unsafe documents."""

    def __init__(self):
        super().__init__("Документ недоступен для защищённой выдачи.")


@dataclass(frozen=True, slots=True)
class ArchivedDocumentDescriptor:
    """Public, non-storage metadata only; does not authorize byte retrieval."""

    document_id: int
    case_id: int
    title: str
    version: int
    file_size: int
    mime_type: str | None


def _valid_source_metadata(document: Document) -> bool:
    digest = str(document.sha256 or "")
    try:
        size = int(document.file_size)
    except (TypeError, ValueError, OverflowError):
        return False
    max_size = max(1, int(settings.max_document_upload_mb)) * 1024 * 1024
    return bool(
        document.security_status == "VERIFIED"
        and has_usable_document_envelope(document)
        and len(digest) == 64
        and all(char in "0123456789abcdef" for char in digest)
        and 0 < size <= max_size
        and int(document.version or 0) > 0
    )


class ClientArchiveDocumentAccessService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def preflight(
        self, *, user_id: int, case_id: int, document_id: int
    ) -> ArchivedDocumentDescriptor:
        """Resolve all IDs server-side from authenticated Telegram user context.

        A consistent SQL predicate protects the tenant and checks retention
        state. A returned descriptor is never a grant; any future download
        must repeat this gate and verify actual authenticated ciphertext.
        """
        identifiers = (user_id, case_id, document_id)
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
            for value in identifiers
        ):
            raise ClientArchiveDocumentDenied()

        blocked_retention = (
            select(CaseRetentionRecord.id)
            .where(
                CaseRetentionRecord.case_id == Case.id,
                CaseRetentionRecord.status.in_(RESTRICTED_RETENTION_STATES),
            )
            .exists()
        )
        statement = (
            select(Document)
            .join(Case, Document.case_id == Case.id)
            .join(User, Case.client_id == User.id)
            .where(
                Document.id == document_id,
                Document.case_id == case_id,
                Case.id == case_id,
                Case.client_id == user_id,
                Case.route.in_(("M1", "M2")),
                Case.status.in_(TERMINAL_CASE_STATES),
                Case.content_deleted_at.is_(None),
                User.is_blocked.is_(False),
                ~blocked_retention,
            )
        )
        document = await self.db.scalar(statement)
        if document is None or not _valid_source_metadata(document):
            raise ClientArchiveDocumentDenied()

        return ArchivedDocumentDescriptor(
            document_id=int(document.id),
            case_id=int(document.case_id),
            title=str(document.title),
            version=int(document.version),
            file_size=int(document.file_size),
            mime_type=document.mime_type,
        )
