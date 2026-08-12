from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import require_admin
from app.db.session import get_db
from app.domain.cases.case_timeline import get_client_visible_status
from app.models.audit_log import AuditLog
from app.models.case import Case

router = APIRouter(tags=["admin-workdesk-timeline"])

_ACTOR_LABELS = {
    "client": "Клиент",
    "lawyer": "Юрист",
    "admin": "Администратор",
    "admin_user": "Администратор",
    "operator": "Оператор",
    "staff": "Команда",
    "system": "Система",
}

_ACTION_TITLES = {
    "CLIENT_MESSAGE_CREATED": "Клиент написал сообщение",
    "LAWYER_MESSAGE_CREATED": "Команда ответила клиенту",
    "CASE_SLA_STARTED": "Запущен контроль SLA",
    "CASE_SLA_CLEARED": "Контроль SLA сброшен",
    "CASE_SLA_LAWYER_ACTIVITY": "Зафиксировано действие юриста",
    "CASE_SLA_STATUS_SYNCHRONIZED": "SLA синхронизирован с этапом дела",
    "CASE_SLA_ACKNOWLEDGED": "Просрочка SLA обработана",
    "CASE_SLA_FIRST_RESPONSE_OVERDUE": "Просрочена первая реакция юриста",
    "CASE_SLA_ACTION_OVERDUE": "Просрочено следующее действие юриста",
    "DOCUMENT_UPLOADED": "Документ загружен",
    "DOCUMENT_STATUS_CHANGED": "Решение по документу обновлено",
    "PAYMENT_PAID": "Оплата подтверждена",
    "M1_MONEY_RECEIVED": "Зафиксировано фактическое взыскание",
    "M1_CLAIM_SENT": "Претензия направлена",
    "M1_COURT_OPENED": "Судебный этап открыт",
    "M1_CLOSED": "Дело закрыто",
    "M2_CONSULTATION_BOOKED": "Консультация назначена",
    "M2_CONSULTATION_DONE": "Консультация проведена",
    "M2_CLOSED": "Консультационное обращение закрыто",
}

_HIDDEN_ACTION_MARKERS = (
    "LOGIN",
    "LOGOUT",
    "SESSION",
    "TOKEN",
    "HEARTBEAT",
    "VIEWED",
)


def _humanize_action(action: str) -> str:
    key = str(action or "").strip().upper()
    if key in _ACTION_TITLES:
        return _ACTION_TITLES[key]
    words = key.replace("_", " ").strip().lower()
    if not words:
        return "Событие по делу"
    return words[:1].upper() + words[1:]


def _category(action: str) -> str:
    key = str(action or "").upper()
    if "SLA" in key:
        return "sla"
    if "PAYMENT" in key or "REFUND" in key or "FEE" in key:
        return "payments"
    if "DOCUMENT" in key or "DOCS" in key or "POA" in key:
        return "documents"
    if "MESSAGE" in key:
        return "messages"
    if "CONSULT" in key or key.startswith("M2_"):
        return "consultation"
    if any(
        marker in key
        for marker in ("CLAIM", "COURT", "ENFORCEMENT", "MONEY_RECEIVED")
    ):
        return "legal_stage"
    if "CALCUL" in key:
        return "calculation"
    return "case"


def _status_detail(row: AuditLog) -> str | None:
    old_value = row.old_value if isinstance(row.old_value, dict) else {}
    new_value = row.new_value if isinstance(row.new_value, dict) else {}
    old_status = old_value.get("status") or old_value.get("case_status")
    new_status = new_value.get("status") or new_value.get("case_status")
    if old_status and new_status and str(old_status) != str(new_status):
        return (
            f"{get_client_visible_status(str(old_status))} → "
            f"{get_client_visible_status(str(new_status))}"
        )
    return None


def _event_detail(row: AuditLog) -> str | None:
    comment = str(row.comment or "").strip()
    if comment:
        return comment[:700]
    return _status_detail(row)


def _visible_event(row: AuditLog) -> bool:
    key = str(row.action or "").upper()
    return not any(marker in key for marker in _HIDDEN_ACTION_MARKERS)


def _serialize_event(row: AuditLog) -> dict[str, object]:
    return {
        "id": int(row.id),
        "occurred_at": row.created_at.isoformat() if row.created_at else None,
        "category": _category(row.action),
        "actor_label": _ACTOR_LABELS.get(
            str(row.actor_type or "").lower(),
            "Команда",
        ),
        "title": _humanize_action(row.action),
        "detail": _event_detail(row),
    }


@router.get("/admin/workdesk/cases/{case_id}/timeline")
async def workdesk_case_timeline(
    case_id: int,
    limit: int = Query(default=6, ge=1, le=20),
    before_id: int | None = Query(default=None, ge=1),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    case = await db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Дело не найдено")

    query = select(AuditLog).where(
        AuditLog.entity_type == "case",
        AuditLog.entity_id == case_id,
    )
    if before_id is not None:
        query = query.where(AuditLog.id < before_id)

    # Fetch extra rows because a small number of technical case audit events may
    # be hidden from the human timeline. Pagination stays cursor-based and never
    # exposes raw old/new JSON values to the browser.
    scan_limit = min(limit * 4 + 1, 81)
    rows = list(
        (
            await db.execute(
                query.order_by(AuditLog.id.desc()).limit(scan_limit)
            )
        ).scalars().all()
    )
    visible = [row for row in rows if _visible_event(row)]
    page = visible[:limit]
    has_more = len(visible) > limit or len(rows) >= scan_limit
    if has_more and page:
        next_before_id = int(page[-1].id)
    elif has_more and rows:
        # Even a block containing only hidden technical events must advance the
        # cursor, otherwise an older meaningful event becomes unreachable.
        next_before_id = int(rows[-1].id)
    else:
        next_before_id = None

    return {
        "case_id": case_id,
        "count": len(page),
        "items": [_serialize_event(row) for row in page],
        "has_more": has_more,
        "next_before_id": next_before_id,
    }