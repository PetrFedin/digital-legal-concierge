from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_user import AdminUser
from app.security.keyring import mfa_encryption_ring
from app.security.mfa import reencrypt_secret, secret_needs_reencryption


async def reencrypt_mfa_secrets(db: AsyncSession, *, limit: int = 100) -> int:
    """Re-encrypt decryptable MFA secrets with the active encryption key.

    Previous keys are read-only grace keys. This job progressively removes the
    dependency on them without forcing users to reset MFA during a rotation.
    Already rotated rows are excluded in SQL so batches continue beyond the
    first ``limit`` users on large installations.
    """

    active = mfa_encryption_ring().require_active()
    active_prefix = f"v2:{active.key_id}:"
    users = (
        await db.execute(
            select(AdminUser)
            .where(
                AdminUser.mfa_secret_encrypted.is_not(None),
                AdminUser.mfa_secret_encrypted.not_like(f"{active_prefix}%"),
            )
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
