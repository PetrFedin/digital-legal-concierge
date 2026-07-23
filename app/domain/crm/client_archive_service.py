from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.analytics.activity_service import (
    CRM_CLIENT_ARCHIVED,
    CRM_CLIENT_RESTORED,
    ActivityService,
    load_client_archive_states,
)
from app.models.user import User


class CRMClientArchiveService:
    """Manage client archive state through append-only analytics events.

    The user row is locked while the latest state is read and the new event is
    written. Repeating the same request is therefore safe and does not create
    duplicate archive/restore events.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_state(self, user_id: int) -> bool:
        user = (
            await self.db.execute(select(User.id).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            raise LookupError("client not found")
        states = await load_client_archive_states(self.db, [user_id])
        return bool(states.get(user_id, False))

    async def set_state(
        self,
        *,
        user_id: int,
        archived: bool,
        actor_id: int | None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        user = (
            await self.db.execute(
                select(User).where(User.id == user_id).with_for_update()
            )
        ).scalar_one_or_none()
        if user is None:
            raise LookupError("client not found")

        states = await load_client_archive_states(self.db, [user_id])
        current = bool(states.get(user_id, False))
        target = bool(archived)
        if current == target:
            return {
                "user_id": user_id,
                "archived": current,
                "changed": False,
            }

        event_name = CRM_CLIENT_ARCHIVED if target else CRM_CLIENT_RESTORED
        await ActivityService(self.db).record(
            event_name=event_name,
            user_id=user_id,
            source="admin_crm",
            payload={
                "actor_id": actor_id,
                "reason": (reason or "").strip() or None,
                "previous_archived": current,
                "archived": target,
            },
        )
        return {
            "user_id": user_id,
            "archived": target,
            "changed": True,
        }
