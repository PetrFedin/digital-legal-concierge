from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_user import AdminUser
from app.security.mfa import reencrypt_secret, secret_needs_reencryption


async def reencrypt_mfa_secrets(db: AsyncSession, *, limit: int = 100) -> int:
    """Re-encrypt decryptable MFA secrets with the active encryption key.

    Previous keys are read-only grace keys. This job progressively removes the
    dependency on them without forcing users to reset MFA during a rotation.
    """

    users = (
        await db.execute(
            select(AdminUser)
            .where(AdminUser.mfa_secret_encrypted.is_not(None))
            .order_by(AdminUser.id.asc())
            .limit(max(1, int(limit)))
            .with_for_update()
        )
    ).scalars().all()

    rotated = 0
    for user in users:
        value = user.mfa_secret_encrypted
        if not secret_needs_reencryption(value):
            continue
        encrypted = reencrypt_secret(value)
        if encrypted and encrypted != value:
            user.mfa_secret_encrypted = encrypted
            rotated += 1
    if rotated:
        await db.flush()
    return rotated
