from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.notifications.notification_sender import NotificationSender


def _empty_delivery() -> dict[str, object]:
    return {
        "status": "not_required",
        "requested": 0,
        "processed": 0,
        "sent": 0,
        "retry": 0,
        "failed": 0,
    }


def _queued_delivery(
    notification_ids: tuple[int, ...],
    *,
    reason: str,
) -> dict[str, object]:
    return {
        "status": "queued",
        "requested": len(notification_ids),
        "processed": 0,
        "sent": 0,
        "retry": len(notification_ids),
        "failed": 0,
        "reason": reason,
    }


def _delivery_status(
    summary: dict[str, int],
    *,
    requested: int,
) -> str:
    sent = int(summary.get("sent", 0))
    retry = int(summary.get("retry", 0))
    failed = int(summary.get("failed", 0))
    processed = int(summary.get("processed", 0))
    if requested and sent >= requested:
        return "delivered"
    if retry:
        return "queued"
    if failed:
        return "failed"
    if processed == 0:
        return "already_processing"
    return "queued"


async def deliver_selected_notifications(
    db: AsyncSession,
    notification_ids: list[int] | tuple[int, ...],
    *,
    timeout_seconds: float = 8.0,
) -> dict[str, object]:
    """Try selected durable outbox rows without risking the saved operation.

    The caller must commit the business operation and outbox rows before this
    function is called. Telegram failures then affect only delivery state; the
    legal message, review decision or other business operation stays durable.
    """

    ids = tuple(sorted({int(item) for item in notification_ids if int(item) > 0}))
    if not ids:
        return _empty_delivery()

    try:
        summary = await asyncio.wait_for(
            NotificationSender(db).send_selected(ids),
            timeout=timeout_seconds,
        )
        await db.commit()
    except TimeoutError:
        await db.rollback()
        return _queued_delivery(ids, reason="telegram_timeout")
    except Exception:
        await db.rollback()
        return _queued_delivery(ids, reason="delivery_error")

    requested = int(summary.get("requested", len(ids)))
    return {
        "status": _delivery_status(summary, requested=requested),
        **summary,
    }
