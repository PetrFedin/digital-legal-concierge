from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.notifications.notification_engine import NotificationEngine
from app.models.audit_log import AuditLog
from app.models.case import Case


async def add_case_history_event(
    db: AsyncSession,
    *,
    actor_type: str,
    actor_id: int | None,
    case_id: int,
    action: str,
    old_value: dict | None = None,
    new_value: dict | None = None,
    comment: str | None = None,
):
    event = AuditLog(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity_type="case",
        entity_id=case_id,
        old_value=old_value,
        new_value=new_value,
        comment=comment,
    )
    db.add(event)
    await db.flush()

    # Cancellation is a state-changing client event and must be visible even
    # when it is initiated outside Telegram. Keep this mapping intentionally
    # narrow; other history actions remain free of notification side effects.
    if action == "CONSULTATION_CANCELLED":
        client_id = await db.scalar(
            select(Case.client_id).where(Case.id == case_id)
        )
        if client_id is not None:
            await NotificationEngine(db).emit(
                event_code="CONSULTATION_CANCELLED",
                case_id=case_id,
                user_id=client_id,
            )

    return event
