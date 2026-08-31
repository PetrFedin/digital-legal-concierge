from __future__ import annotations

from typing import Any

from fastapi import Depends, Header, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.payment_review_center import require_admin
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.payment import Payment


PAYMENT_REVIEW_HISTORY_LIMIT = 20
PAYMENT_REVIEW_REQUIRED = "CONSULTATION_PAYMENT_REVIEW_REQUIRED"
PAYMENT_REVIEW_RESOLVED = "CONSULTATION_PAYMENT_REVIEW_RESOLVED"
PAYMENT_REVIEW_HISTORY_ACTIONS = (
    PAYMENT_REVIEW_REQUIRED,
    PAYMENT_REVIEW_RESOLVED,
)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _status(value: Any) -> str | None:
    raw = getattr(value, "value", value)
    return _text(raw)


def _positive_int(value: Any) -> int | None:
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _event_payment_id(event: AuditLog) -> int | None:
    return _positive_int(_mapping(event.new_value).get("payment_id"))


def serialize_payment_review_history_event(event: AuditLog) -> dict[str, Any]:
    """Project one audit event into a deliberately small staff-safe contract.

    AuditLog contains internal integrity fields and free-text comments. The history
    API must not become a raw audit export: it exposes only the business facts
    needed to explain when a payment entered review and how that exact review was
    resolved. Provider payloads, reservation keys, comments and integrity metadata
    remain available only through their dedicated internal evidence paths.
    """

    old_value = _mapping(event.old_value)
    new_value = _mapping(event.new_value)
    kind = "required" if event.action == PAYMENT_REVIEW_REQUIRED else "resolved"
    item: dict[str, Any] = {
        "event_id": int(event.id),
        "kind": kind,
        "action": str(event.action),
        "actor_type": _text(event.actor_type),
        "actor_id": int(event.actor_id) if event.actor_id is not None else None,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "origin_status": _status(
            old_value.get("payment_status", old_value.get("status"))
        ),
        "resulting_status": _status(
            new_value.get("payment_status", new_value.get("status"))
        ),
    }
    if kind == "required":
        item["reason"] = _text(new_value.get("reason"))
        return item

    item.update(
        {
            "decision": _text(new_value.get("decision")),
            "consultation_id": _positive_int(new_value.get("consultation_id")),
            "slot_id": _positive_int(new_value.get("slot_id")),
            "orphan_consultation_id": _positive_int(
                new_value.get("orphan_consultation_id")
            ),
            "orphan_slot_id": _positive_int(new_value.get("orphan_slot_id")),
        }
    )
    return item


async def load_payment_review_history(
    db: AsyncSession,
    *,
    payment_id: int,
) -> dict[str, Any]:
    """Load durable review history for one exact payment, including terminal rows."""

    payment = await db.get(Payment, int(payment_id))
    if payment is None:
        raise HTTPException(status_code=404, detail="Платёж не найден")

    events = list(
        (
            await db.execute(
                select(AuditLog)
                .where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id == int(payment.case_id),
                    AuditLog.action.in_(PAYMENT_REVIEW_HISTORY_ACTIONS),
                )
                .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            )
        ).scalars().all()
    )
    matching = [
        event for event in events if _event_payment_id(event) == int(payment.id)
    ]
    visible = matching[:PAYMENT_REVIEW_HISTORY_LIMIT]
    return {
        "payment_id": int(payment.id),
        "case_id": int(payment.case_id),
        "payment_status": _status(payment.status),
        "event_count": len(visible),
        "total_event_count": len(matching),
        "truncated": len(matching) > PAYMENT_REVIEW_HISTORY_LIMIT,
        "events": [serialize_payment_review_history_event(event) for event in visible],
    }


async def get_payment_review_history(
    payment_id: int,
    response: Response,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Return a normalized, read-only audit timeline for one reviewed payment."""

    require_admin(x_admin_token)
    response.headers["Cache-Control"] = "no-store"
    return await load_payment_review_history(db, payment_id=payment_id)
