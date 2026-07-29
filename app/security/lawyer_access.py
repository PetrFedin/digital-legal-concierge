from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_user import AdminUser
from app.models.lawyer import Lawyer
from app.security.access_control import (
    ROLE_LAWYER,
    decode_access_token,
    has_role,
)


@dataclass(frozen=True)
class LawyerActor:
    account: AdminUser
    lawyer: Lawyer
    token_payload: dict

    @property
    def actor_id(self) -> int:
        return self.lawyer.id


async def require_lawyer_actor(
    db: AsyncSession,
    token: str | None,
) -> LawyerActor:
    payload = decode_access_token(token)
    if not payload or not has_role(payload.get("roles"), ROLE_LAWYER):
        raise HTTPException(
            status_code=403,
            detail="Доступ только для пользователя с ролью юриста",
        )

    try:
        account_id = int(payload.get("uid") or 0)
    except (TypeError, ValueError):
        account_id = 0
    if not account_id:
        raise HTTPException(
            status_code=403,
            detail=(
                "Legacy-токен нельзя использовать для действий юриста. "
                "Войдите под персональной учётной записью."
            ),
        )

    account = await db.get(AdminUser, account_id)
    if not account or not account.is_active:
        raise HTTPException(
            status_code=403,
            detail="Учётная запись юриста неактивна или не найдена",
        )

    conditions = []
    if account.email:
        conditions.append(Lawyer.email == account.email)
    if account.telegram_id is not None:
        conditions.append(Lawyer.telegram_id == account.telegram_id)
    if not conditions:
        raise HTTPException(
            status_code=409,
            detail=(
                "Учётная запись юриста не связана с карточкой юриста. "
                "Укажите email или Telegram ID в управлении доступом."
            ),
        )

    lawyer = (
        await db.execute(
            select(Lawyer)
            .where(or_(*conditions))
            .order_by(Lawyer.id.asc())
        )
    ).scalars().first()
    if not lawyer:
        raise HTTPException(
            status_code=409,
            detail="Карточка юриста для учётной записи не найдена",
        )
    if not lawyer.is_active:
        raise HTTPException(
            status_code=403,
            detail="Карточка юриста отключена",
        )

    changed = False
    if account.telegram_id is not None and lawyer.telegram_id != account.telegram_id:
        lawyer.telegram_id = account.telegram_id
        changed = True
    if account.full_name and lawyer.full_name != account.full_name:
        lawyer.full_name = account.full_name
        changed = True
    if changed:
        await db.flush()

    return LawyerActor(
        account=account,
        lawyer=lawyer,
        token_payload=payload,
    )
