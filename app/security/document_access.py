from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.cases.case_responsibility import lawyer_can_access_case
from app.models.admin_user import AdminUser
from app.models.case import Case
from app.models.document import Document
from app.models.document_access_grant import DocumentAccessGrant
from app.models.document_derivative import DERIVATIVE_READY, DocumentDerivative
from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_LAWYER,
    ROLE_SUPERADMIN,
    decode_access_token,
    has_role,
    normalize_roles,
)
from app.security.document_encryption import ENCRYPTION_STATUS, FORMAT_V2
from app.security.keyring import active_hmac_digest, hmac_candidates
from app.security.lawyer_access import require_lawyer_actor
from app.security.security_events import pseudonymize_security_value


class DocumentAccessError(HTTPException):
    def __init__(self, status_code: int, detail: str, reason: str):
        super().__init__(status_code=status_code, detail=detail)
        self.reason = reason


@dataclass(frozen=True)
class DocumentActor:
    account: AdminUser
    payload: dict
    role: str
    lawyer_id: int | None = None

    @property
    def account_id(self) -> int:
        return int(self.account.id)


@dataclass(frozen=True)
class IssuedDocumentGrant:
    grant: DocumentAccessGrant
    secret: str


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _grant_message(public_id: str, secret: str) -> bytes:
    return f"document-access:{public_id}:{secret}".encode("utf-8")


def grant_cookie_name(public_id: str) -> str:
    return f"dlc_doc_grant_{public_id}"


def grant_download_path(public_id: str) -> str:
    return f"/document-access/grants/{public_id}/download"


async def resolve_document_actor(
    db: AsyncSession,
    token: str | None,
) -> DocumentActor:
    payload = decode_access_token(token)
    if not payload or payload.get("legacy"):
        raise DocumentAccessError(
            401,
            "Для доступа к документам требуется персональная учётная запись",
            "personal_session_required",
        )
    try:
        account_id = int(payload.get("uid") or 0)
    except (TypeError, ValueError):
        account_id = 0
    account = await db.get(AdminUser, account_id) if account_id else None
    if not account or not account.is_active:
        raise DocumentAccessError(401, "Учётная запись отключена", "inactive_account")

    current_roles = normalize_roles(account.role)
    token_roles = normalize_roles(payload.get("roles"))
    if set(current_roles) != set(token_roles):
        raise DocumentAccessError(401, "Права учётной записи изменены", "roles_changed")
    if int(payload.get("sv") or 0) != int(account.session_version or 1):
        raise DocumentAccessError(401, "Сессия отозвана", "session_revoked")
    if not str(payload.get("jti") or ""):
        raise DocumentAccessError(401, "Сессия не содержит идентификатор", "missing_jti")

    if has_role(current_roles, ROLE_SUPERADMIN):
        if not account.mfa_enabled or not bool(payload.get("mfa")):
            raise DocumentAccessError(403, "Требуется подтверждённая MFA-сессия", "mfa_required")
        return DocumentActor(account=account, payload=payload, role=ROLE_SUPERADMIN)
    if has_role(current_roles, ROLE_ADMIN):
        return DocumentActor(account=account, payload=payload, role=ROLE_ADMIN)
    if has_role(current_roles, ROLE_LAWYER):
        lawyer_actor = await require_lawyer_actor(db, token)
        return DocumentActor(
            account=account,
            payload=payload,
            role=ROLE_LAWYER,
            lawyer_id=lawyer_actor.lawyer.id,
        )
    raise DocumentAccessError(403, "Недостаточно прав для документов", "role_denied")


async def load_authorized_document(
    db: AsyncSession,
    *,
    actor: DocumentActor,
    document_id: int,
) -> tuple[Document, Case]:
    document = await db.get(Document, int(document_id))
    if not document:
        raise DocumentAccessError(404, "Документ не найден", "document_not_found")
    case = await db.get(Case, document.case_id)
    if not case:
        raise DocumentAccessError(404, "Дело документа не найдено", "case_not_found")
    if actor.role == ROLE_LAWYER and not await lawyer_can_access_case(
        db,
        case=case,
        lawyer_id=actor.lawyer_id,
    ):
        raise DocumentAccessError(
            403,
            "Документ относится к делу, за которое текущий юрист не отвечает",
            "lawyer_not_responsible",
        )
    if document.security_status != "VERIFIED":
        raise DocumentAccessError(409, "Документ не прошёл проверку безопасности", "not_verified")
    if document.encryption_status != ENCRYPTION_STATUS:
        raise DocumentAccessError(409, "Документ ещё не зашифрован", "not_encrypted")
    if document.data_key_destroyed_at is not None:
        raise DocumentAccessError(410, "Ключ документа уничтожен", "document_key_destroyed")
    if (
        int(document.encryption_format_version or 0) != FORMAT_V2
        or not document.encryption_key_id
        or not document.encryption_envelope_id
        or not document.encrypted_data_key
        or not document.encrypted_data_key_nonce
    ):
        raise DocumentAccessError(
            409,
            "Документ ожидает миграции защищённого хранилища",
            "envelope_migration_required",
        )
    return document, case


async def load_authorized_derivative(
    db: AsyncSession,
    *,
    actor: DocumentActor,
    derivative_id: int,
) -> tuple[DocumentDerivative, Document, Case]:
    """Authorize a derivative only through its immutable source Document/Case."""

    derivative = await db.get(DocumentDerivative, int(derivative_id))
    if not derivative:
        raise DocumentAccessError(
            404,
            "Производный документ не найден",
            "derivative_not_found",
        )
    source, case = await load_authorized_document(
        db,
        actor=actor,
        document_id=int(derivative.source_document_id),
    )
    if (
        int(derivative.case_id) != int(case.id)
        or str(derivative.source_sha256 or "") != str(source.sha256 or "")
    ):
        raise DocumentAccessError(
            409,
            "Связь производного документа с исходником нарушена",
            "derivative_lineage_mismatch",
        )
    if derivative.status != DERIVATIVE_READY:
        raise DocumentAccessError(
            409,
            "Производный документ ещё не готов",
            "derivative_not_ready",
        )
    if (
        derivative.encryption_status != ENCRYPTION_STATUS
        or int(derivative.encryption_format_version or 0) != FORMAT_V2
        or not derivative.file_path
        or not derivative.sha256
        or not derivative.encryption_key_id
        or not derivative.encryption_envelope_id
        or not derivative.encrypted_data_key
        or not derivative.encrypted_data_key_nonce
    ):
        raise DocumentAccessError(
            409,
            "Производный документ не прошёл защищённое хранение",
            "derivative_envelope_invalid",
        )
    if derivative.data_key_destroyed_at is not None:
        raise DocumentAccessError(
            410,
            "Ключ производного документа уничтожен",
            "derivative_key_destroyed",
        )
    return derivative, source, case


async def issue_document_grant(
    db: AsyncSession,
    *,
    actor: DocumentActor,
    document: Document,
    case: Case,
    client_address: str | None,
) -> IssuedDocumentGrant:
    now = datetime.now(timezone.utc)
    ttl = min(max(int(settings.document_access_grant_ttl_seconds), 30), 300)
    max_active = min(max(int(settings.document_access_max_active_grants), 1), 20)

    # Serialize issuance per account. Without this lock, two tabs can both read
    # the same active-grant count and exceed the configured security limit.
    await db.execute(
        select(AdminUser.id)
        .where(AdminUser.id == actor.account_id)
        .with_for_update()
    )
    active = (
        await db.execute(
            select(DocumentAccessGrant)
            .where(
                DocumentAccessGrant.actor_account_id == actor.account_id,
                DocumentAccessGrant.used_at.is_(None),
                DocumentAccessGrant.revoked_at.is_(None),
                DocumentAccessGrant.expires_at > now,
            )
            .order_by(DocumentAccessGrant.created_at.asc())
        )
    ).scalars().all()
    for stale in active[: max(0, len(active) - max_active + 1)]:
        stale.revoked_at = now

    public_id = uuid4().hex
    secret = secrets.token_urlsafe(32)
    token_key_id, token_digest = active_hmac_digest(_grant_message(public_id, secret))
    grant = DocumentAccessGrant(
        public_id=public_id,
        document_id=document.id,
        case_id=case.id,
        actor_account_id=actor.account_id,
        actor_role=actor.role,
        session_jti_ref=pseudonymize_security_value("session-jti", actor.payload["jti"]),
        session_version=int(actor.payload.get("sv") or 0),
        token_key_id=token_key_id,
        token_digest=token_digest,
        purpose="download",
        expires_at=now + timedelta(seconds=ttl),
        client_ref=pseudonymize_security_value("document-client", client_address),
    )
    db.add(grant)
    await db.flush()
    return IssuedDocumentGrant(grant=grant, secret=secret)


async def consume_document_grant(
    db: AsyncSession,
    *,
    actor: DocumentActor,
    public_id: str,
    secret: str | None,
) -> tuple[DocumentAccessGrant, Document, Case]:
    now = datetime.now(timezone.utc)
    grant = (
        await db.execute(
            select(DocumentAccessGrant).where(DocumentAccessGrant.public_id == public_id)
        )
    ).scalar_one_or_none()
    if not grant:
        raise DocumentAccessError(404, "Разрешение не найдено", "grant_not_found")
    if grant.actor_account_id != actor.account_id:
        raise DocumentAccessError(403, "Разрешение выдано другой учётной записи", "actor_mismatch")
    expected_session = pseudonymize_security_value("session-jti", actor.payload.get("jti"))
    if not hmac.compare_digest(grant.session_jti_ref, expected_session or ""):
        raise DocumentAccessError(403, "Разрешение привязано к другой сессии", "session_mismatch")
    if grant.session_version != int(actor.payload.get("sv") or 0):
        raise DocumentAccessError(401, "Сессия разрешения отозвана", "session_version_mismatch")
    if grant.revoked_at is not None:
        raise DocumentAccessError(410, "Разрешение отозвано", "grant_revoked")
    if grant.used_at is not None:
        raise DocumentAccessError(410, "Разрешение уже использовано", "grant_reused")
    if _utc(grant.expires_at) <= now:
        raise DocumentAccessError(410, "Разрешение истекло", "grant_expired")
    if not secret:
        raise DocumentAccessError(401, "Секрет разрешения отсутствует", "grant_secret_missing")

    valid_secret = any(
        key_id == grant.token_key_id and hmac.compare_digest(digest, grant.token_digest)
        for key_id, digest in hmac_candidates(_grant_message(public_id, secret))
    )
    if not valid_secret:
        raise DocumentAccessError(403, "Секрет разрешения недействителен", "grant_secret_invalid")

    document, case = await load_authorized_document(
        db,
        actor=actor,
        document_id=grant.document_id,
    )
    claimed = await db.execute(
        update(DocumentAccessGrant)
        .where(
            DocumentAccessGrant.id == grant.id,
            DocumentAccessGrant.used_at.is_(None),
            DocumentAccessGrant.revoked_at.is_(None),
            DocumentAccessGrant.expires_at > now,
        )
        .values(used_at=now)
    )
    if claimed.rowcount != 1:
        raise DocumentAccessError(410, "Разрешение уже погашено", "grant_race_lost")
    grant.used_at = now
    return grant, document, case


async def cleanup_document_access_grants(db: AsyncSession) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    result = await db.execute(
        delete(DocumentAccessGrant).where(
            (DocumentAccessGrant.expires_at < cutoff)
            | (DocumentAccessGrant.used_at < cutoff)
            | (DocumentAccessGrant.revoked_at < cutoff)
        )
    )
    return int(result.rowcount or 0)