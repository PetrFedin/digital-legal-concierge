from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.revoked_access_token import RevokedAccessToken
from app.security.access_control import decode_access_token


def token_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _expires_at(payload: dict) -> datetime:
    return datetime.fromtimestamp(int(payload.get("exp") or 0), tz=timezone.utc)


async def is_token_revoked(db: AsyncSession, token: str | None) -> bool:
    if not token:
        return False
    digest = token_hash(token)
    row = (
        await db.execute(
            select(RevokedAccessToken.id).where(
                RevokedAccessToken.token_hash == digest
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def revoke_token(
    db: AsyncSession,
    token: str | None,
    *,
    reason: str = "logout",
    comment: str | None = None,
) -> bool:
    payload = decode_access_token(token)
    if not token or not payload or payload.get("legacy"):
        return False
    expires_at = _expires_at(payload)
    if expires_at <= datetime.now(timezone.utc):
        return False
    digest = token_hash(token)
    existing = (
        await db.execute(
            select(RevokedAccessToken).where(
                RevokedAccessToken.token_hash == digest
            )
        )
    ).scalar_one_or_none()
    if existing:
        return False
    try:
        user_id = int(payload.get("uid") or 0) or None
    except (TypeError, ValueError):
        user_id = None
    db.add(
        RevokedAccessToken(
            token_hash=digest,
            user_id=user_id,
            expires_at=expires_at,
            reason=str(reason or "logout")[:100],
            comment=(str(comment).strip()[:1000] if comment else None),
        )
    )
    await db.flush()
    return True


async def cleanup_revoked_tokens(db: AsyncSession) -> int:
    result = await db.execute(
        delete(RevokedAccessToken).where(
            RevokedAccessToken.expires_at <= datetime.now(timezone.utc)
        )
    )
    return int(result.rowcount or 0)
