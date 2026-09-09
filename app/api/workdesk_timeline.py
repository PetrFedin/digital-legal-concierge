from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import require_admin
from app.api.technical_case_recovery import router as technical_case_recovery_router
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
    "DOCUMENTS_SENT_TO_REVIEW": "Документы переданы юридической команде",
    "DOCUMENT_REVIEW_DECISION": "Юрист принял решение по документу",
    "SERVICE_CONTRACT_PUBLISHED": "Опубликована версия договора",
    "CLIENT_SERVICE_CONTRACT_CONFIRMED": "Клиент подтвердил текущую версию договора",
    "CLIENT_POA_READY_REPORTED": "Клиент сообщил о готовности доверенности",
    "M1_POA_RECEIVED_CONFIRMED": "Юрист подтвердил получение доверенности",
    "PAYMENT_PAID": "Оплата подтверждена",
    "M1_STALE_PAYMENT_REFUND_REQUIRED": "Поздний платёж направлен в возврат",
    "M1_PAYMENT_REFUND_COMPLETED": "Возврат M1 отмечен выполненным",
    "M1_PAYMENT_REFUND_DECLINED": "По возврату M1 зафиксирован отказ",
    "M1_PAYMENT_REFUND_REOPENED": "Возврат M1 повторно открыт после отказа",
    "M1_MONEY_RECEIVED": "Зафиксировано фактическое взыскание",
    "M1_CLAIM_SENT": "Претензия направлена",
    "M1_COURT_OPENED": "Судебный этап открыт",
    "COURT_STAGE_STARTED": "Судебный этап открыт",
    "M1_COURT_DECISION_RECORDED": "Зафиксирован судебный акт",
    "COURT_PAYMENT_OPENED": "Открыт второй платёж после судебного акта",
    "M1_CLOSED": "Дело закрыто",
    "M2_CONSULTATION_BOOKED": "Консультация назначена",
    "CONSULTATION_BOOKED_AFTER_PAYMENT": "Оплата применена, консультация подтверждена",
    "CONSULTATION_SLOT_HOLD_EXPIRED": "Истёк резерв времени консультации",
    "CONSULTATION_COMPLETED": "Зафиксирован результат консультации",
    "CONSULTATION_LEGACY_OUTCOME_RESOLVED": "Уточнён итог старой консультации",
    "CONSULTATION_CLIENT_NO_SHOW": "Зафиксирована неявка клиента",
    "CONSULTATION_CLIENT_NO_SHOW_REBOOKING_OPENED": "После неявки клиента открыта новая запись",
    "CONSULTATION_CLIENT_NO_SHOW_CASE_CLOSED": "Обращение закрыто после неявки клиента",
    "CONSULTATION_LAWYER_NO_SHOW": "Зафиксирована неявка юриста",
    "CONSULTATION_REBOOKED_AFTER_LAWYER_NO_SHOW": "Консультация бесплатно перенесена",
    "CONSULTATION_LAWYER_NO_SHOW_REFUND_REQUESTED": "Возврат открыт после неявки юриста",
    "CONSULTATION_CANCELLATION_REQUESTED": "Консультация отменена, возврат передан в работу",
    "CONSULTATION_REFUND_COMPLETED": "Возврат консультации отмечен выполненным",
    "CONSULTATION_REFUND_DECLINED": "По возврату консультации зафиксирован отказ",
    "CONSULTATION_REFUND_REOPENED": "Возврат консультации повторно открыт после отказа",
    "M1_INTERNAL_PAYMENT_STAGE_RECOVERED": "Восстановлен зависший платёжный этап",
    "M2_CONSULTATION_DONE": "Консультация проведена",
    "M2_CLOSED": "Консультационное обращение закрыто",
    "CASE_ERROR_RECOVERED_TO_LAST_SAFE_STATUS": "Техническая блокировка снята",
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
    if "CONTRACT" in key:
        return "contract"
    if "DOCUMENT" in key or "DOCS" in key or "POA" in key:
        return "documents"
    if "MESSAGE" in key:
        return "messages"
    if "CONSULT" in key or "SLOT_HOLD" in key or key.startswith("M2_"):
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


# Technical Case recovery remains colocated with the timeline for now. M1
# payment-stage recovery is intentionally not re-mounted here: it already has a
# separate runtime owner, and mounting the same router through the timeline made
# those financial recovery endpoints depend on route-registration order.
router.include_router(technical_case_recovery_router)

__all__ = ["router", "workdesk_case_timeline"]
