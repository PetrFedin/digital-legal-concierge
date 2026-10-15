"""Single-claim client grant authority. No file reads, delivery, or public routes."""
from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.documents.client_archive_access import (
    ClientArchiveDocumentAccessService, ClientArchiveDocumentDenied,
)
from app.models.client_document_grant import ClientDocumentGrant
from app.models.case import Case
from app.models.document import Document
from app.models.user import User
from app.security.keyring import active_hmac_digest, hmac_candidates

TTL_SECONDS = 120
CHANNEL = "PRIVATE_PORTAL"


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _payload(public_id: str, secret: str) -> bytes:
    return f"client-document-grant:v1:{public_id}:{secret}".encode()


class ClientGrantDenied(ValueError):
    def __init__(self):
        super().__init__("Разрешение недоступно или уже использовано.")


@dataclass(frozen=True)
class IssuedClientGrant:
    public_id: str
    secret: str
    expires_at: datetime


class ClientDocumentGrantService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def issue(self, *, user_id: int, case_id: int, document_id: int,
                    approval_reference: str) -> IssuedClientGrant:
        if not isinstance(approval_reference, str) or not approval_reference.strip() or len(approval_reference) > 120:
            raise ClientGrantDenied()
        # Serialize competing issuance on the authenticated account.
        locked = await self.db.scalar(select(User).where(User.id == user_id).with_for_update())
        if locked is None:
            raise ClientGrantDenied()
        # Share a Case-row lock with retention approval. A concurrent deletion
        # approval must not slip between the owner preflight and grant issue.
        case_locked = await self.db.scalar(
            select(Case.id).where(Case.id == case_id).with_for_update()
        )
        if case_locked is None:
            raise ClientGrantDenied()
        try:
            descriptor = await ClientArchiveDocumentAccessService(self.db).preflight(
                user_id=user_id, case_id=case_id, document_id=document_id
            )
        except ClientArchiveDocumentDenied as error:
            raise ClientGrantDenied() from error
        now = datetime.now(timezone.utc)
        active = (await self.db.execute(
            select(ClientDocumentGrant).where(
                ClientDocumentGrant.user_id == user_id,
                ClientDocumentGrant.document_id == document_id,
                ClientDocumentGrant.status == "ISSUED",
            )
        )).scalars().all()
        for old in active:
            old.status = "REVOKED"
            old.revoked_at = now
        await self.db.flush()
        source = await self.db.get(Document, document_id)
        if source is None:
            raise ClientGrantDenied()
        public_id, secret = uuid4().hex, secrets.token_urlsafe(32)
        key_id, digest = active_hmac_digest(_payload(public_id, secret))
        expiry = now + timedelta(seconds=TTL_SECONDS)
        self.db.add(ClientDocumentGrant(
            public_id=public_id, user_id=user_id, case_id=case_id,
            document_id=document_id, document_version=descriptor.version,
            document_sha256=source.sha256, channel=CHANNEL,
            approval_reference=approval_reference.strip(),
            token_key_id=key_id, token_digest=digest, status="ISSUED",
            expires_at=expiry,
        ))
        await self.db.flush()
        return IssuedClientGrant(public_id, secret, expiry)

    async def claim(self, *, user_id: int, public_id: str, secret: str) -> None:
        """Consume authorization irreversibly; DOES NOT read or send document bytes."""
        if (not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0
            or not isinstance(public_id, str) or len(public_id) != 32
            or not isinstance(secret, str) or not secret):
            raise ClientGrantDenied()
        now = datetime.now(timezone.utc)
        grant = await self.db.scalar(
            select(ClientDocumentGrant).where(
                ClientDocumentGrant.public_id == public_id,
                ClientDocumentGrant.user_id == user_id,
            )
        )
        if grant is None or grant.status != "ISSUED" or _utc(grant.expires_at) <= now:
            raise ClientGrantDenied()
        valid = any(
            kid == grant.token_key_id and hmac.compare_digest(digest, grant.token_digest)
            for kid, digest in hmac_candidates(_payload(public_id, secret))
        )
        if not valid or grant.channel != CHANNEL:
            raise ClientGrantDenied()
        # Retention approval takes this same Case lock before changing state.
        # Keep this transaction open until the caller commits or rolls back.
        case_locked = await self.db.scalar(
            select(Case.id).where(Case.id == grant.case_id).with_for_update()
        )
        if case_locked is None:
            raise ClientGrantDenied()
        try:
            await ClientArchiveDocumentAccessService(self.db).preflight(
                user_id=user_id, case_id=grant.case_id, document_id=grant.document_id
            )
        except ClientArchiveDocumentDenied as error:
            raise ClientGrantDenied() from error
        doc = await self.db.scalar(
            select(Document)
            .where(Document.id == grant.document_id)
            .execution_options(populate_existing=True)
        )
        if (doc is None or doc.version != grant.document_version
            or doc.sha256 != grant.document_sha256):
            raise ClientGrantDenied()
        claimed = await self.db.execute(
            update(ClientDocumentGrant)
            .where(
                ClientDocumentGrant.id == grant.id,
                ClientDocumentGrant.status == "ISSUED",
                ClientDocumentGrant.expires_at > now,
            )
            .values(status="CLAIMED", claimed_at=now)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            raise ClientGrantDenied()
        # Caller must COMMIT this transaction. No plaintext is produced.
