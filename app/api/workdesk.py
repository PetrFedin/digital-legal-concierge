from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import require_admin
from app.api.workdesk_ui import WORKDESK_HTML
from app.config import settings
from app.db.session import get_db
from app.domain.cases.case_timeline import get_client_visible_status
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.lawyer import Lawyer

router = APIRouter(tags=["admin-workdesk"])

CLOSED_STATUSES = ("M1_CLOSED", "M2_CLOSED", "ARCHIVED")
OVERDUE_SLA_STATUSES = ("FIRST_RESPONSE_OVERDUE", "ACTION_OVERDUE")
DOCUMENT_REVIEW_STATUSES = (
    "UPLOADED",
    "ON_REVIEW",
    "PENDING",
    "PENDING_REVIEW",
    "REVIEW_PENDING",
    "REVIEW_REQUIRED",
    "NEEDS_REVIEW",
)
ACTIVE_CONSULTATION_STATUSES = ("BOOKED", "CONFIRMED")

ROUTE_LABELS = {
    "M1": "Ведение дела",
    "M2": "Консультация",
}
SLA_LABELS = {
    "NOT_STARTED": "SLA не запущен",
    "FIRST_RESPONSE_PENDING": "Ожидается первая реакция",
    "FIRST_RESPONSE_OK": "Первая реакция в срок",
    "FIRST_RESPONSE_OVERDUE": "Просрочена первая реакция",
    "ACTION_PENDING": "Ожидается действие",
    "ACTION_OK": "Действие выполнено в срок",
    "ACTION_OVERDUE": "Действие просрочено",
}

_REASON_PRIORITY = {
    "overdue": 0,
    "unassigned": 1,
    "documents": 2,
    "consultation": 3,
}
_REASON_LABELS = {
    "overdue": "Нарушен SLA",
    "unassigned": "Нет ответственного юриста",
    "documents": "Документы ждут решения",
    "consultation": "Консультация сегодня",
}


def _route_label(route: str | None) -> str:
    return ROUTE_LABELS.get(str(route or ""), "Юридическое обращение")


def _sla_label(status: str | None) -> str:
    return SLA_LABELS.get(str(status or ""), "SLA не определён")


def _utc_sort_value(value: datetime | None) -> float:
    if value is None:
        return float("inf")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).timestamp()


def _attention_reasons(
    case: Case,
    *,
    has_documents: bool,
    consultation_at: datetime | None,
) -> list[dict[str, object]]:
    codes: list[str] = []
    if str(case.sla_status or "") in OVERDUE_SLA_STATUSES:
        codes.append("overdue")
    if case.assigned_lawyer_id is None:
        codes.append("unassigned")
    if has_documents:
        codes.append("documents")
    if consultation_at is not None:
        codes.append("consultation")
    return [
        {
            "code": code,
            "label": _REASON_LABELS[code],
            "priority": _REASON_PRIORITY[code],
        }
        for code in sorted(codes, key=_REASON_PRIORITY.__getitem__)
    ]


def _primary_action(
    case: Case,
    reasons: list[dict[str, object]],
) -> dict[str, object]:
    reason_codes = {str(item["code"]) for item in reasons}
    # An overdue case without an owner cannot be recovered until someone owns it.
    if "unassigned" in reason_codes:
        return {
            "kind": "auto_assign",
            "label": "Назначить юриста",
            "endpoint": f"/admin/cases/{case.id}/auto-assign",
            "payload": {
                "expected_lawyer_id": None,
                "expected_status": str(case.status),
            },
        }
    if "overdue" in reason_codes:
        return {
            "kind": "link",
            "label": "Открыть контроль SLA",
            "href": "/admin/sla/ui",
        }
    if "documents" in reason_codes:
        return {
            "kind": "link",
            "label": "Перейти к проверке документов",
            "href": "/document-access/review/ui",
        }
    return {
        "kind": "link",
        "label": "Открыть результаты консультаций",
        "href": "/admin/consultation-outcomes/ui",
    }


def _attention_item(
    case: Case,
    *,
    has_documents: bool,
    consultation_at: datetime | None,
    lawyer_name: str | None,
) -> dict[str, object] | None:
    reasons = _attention_reasons(
        case,
        has_documents=has_documents,
        consultation_at=consultation_at,
    )
    if not reasons:
        return None
    deadline = (
        case.sla_due_at
        if any(item["code"] == "overdue" for item in reasons)
        else consultation_at
        if consultation_at is not None
        else case.created_at
    )
    return {
        "id": case.id,
        "number": case.case_number,
        "route": case.route,
        "route_label": _route_label(case.route),
        "status": str(case.status),
        "status_label": get_client_visible_status(case.status),
        "lawyer_id": case.assigned_lawyer_id,
        "lawyer_name": lawyer_name,
        "next_action": case.next_action
        or "Проверить карточку и определить следующий этап",
        "sla_status": case.sla_status,
        "sla_label": _sla_label(case.sla_status),
        "sla_due_at": case.sla_due_at.isoformat() if case.sla_due_at else None,
        "consultation_at": consultation_at.isoformat() if consultation_at else None,
        "created_at": case.created_at.isoformat(),
        "updated_at": case.updated_at.isoformat(),
        "priority": int(reasons[0]["priority"]),
        "reasons": reasons,
        "primary_action": _primary_action(case, reasons),
        "sort_deadline": _utc_sort_value(deadline),
    }


def _attention_sort_key(item: dict[str, object]) -> tuple[object, ...]:
    return (
        int(item["priority"]),
        float(item["sort_deadline"]),
        str(item["created_at"]),
        int(item["id"]),
    )


@router.get("/admin/workdesk/attention")
async def workdesk_attention(
    limit: int = 12,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    bounded_limit = min(max(int(limit), 1), 50)
    now = datetime.now(timezone.utc)
    today_start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    tomorrow_start = today_start + timedelta(days=1)

    cases = list(
        (
            await db.execute(
                select(Case)
                .where(Case.status.notin_(CLOSED_STATUSES))
                .order_by(Case.created_at.asc(), Case.id.asc())
            )
        ).scalars().all()
    )
    if not cases:
        return {
            "count": 0,
            "total": 0,
            "truncated": False,
            "items": [],
            "generated_at": now.isoformat(),
        }

    case_ids = [case.id for case in cases]
    document_case_ids = set(
        (
            await db.execute(
                select(Document.case_id)
                .where(Document.case_id.in_(case_ids))
                .where(Document.status.in_(DOCUMENT_REVIEW_STATUSES))
                .distinct()
            )
        ).scalars().all()
    )
    consultation_rows = (
        await db.execute(
            select(Consultation.case_id, Consultation.scheduled_at)
            .where(Consultation.case_id.in_(case_ids))
            .where(Consultation.status.in_(ACTIVE_CONSULTATION_STATUSES))
            .where(Consultation.scheduled_at >= today_start)
            .where(Consultation.scheduled_at < tomorrow_start)
            .order_by(Consultation.scheduled_at.asc())
        )
    ).all()
    consultation_times: dict[int, datetime] = {}
    for case_id, scheduled_at in consultation_rows:
        if scheduled_at is None:
            continue
        existing = consultation_times.get(int(case_id))
        if existing is None or (
            _utc_sort_value(scheduled_at) < _utc_sort_value(existing)
        ):
            consultation_times[int(case_id)] = scheduled_at

    lawyer_ids = {
        int(case.assigned_lawyer_id)
        for case in cases
        if case.assigned_lawyer_id is not None
    }
    lawyer_names: dict[int, str] = {}
    if lawyer_ids:
        lawyers = list(
            (
                await db.execute(select(Lawyer).where(Lawyer.id.in_(lawyer_ids)))
            ).scalars().all()
        )
        lawyer_names = {int(item.id): item.full_name for item in lawyers}

    items: list[dict[str, object]] = []
    for case in cases:
        item = _attention_item(
            case,
            has_documents=case.id in document_case_ids,
            consultation_at=consultation_times.get(case.id),
            lawyer_name=lawyer_names.get(case.assigned_lawyer_id),
        )
        if item is not None:
            items.append(item)
    items.sort(key=_attention_sort_key)
    total = len(items)
    visible = items[:bounded_limit]
    for item in visible:
        item.pop("sort_deadline", None)
    return {
        "count": len(visible),
        "total": total,
        "truncated": total > len(visible),
        "items": visible,
        "generated_at": now.isoformat(),
    }


@router.get("/admin/workdesk/ui", response_class=HTMLResponse)
async def workdesk_ui(request: Request):
    token = request.headers.get("x-admin-token") or request.cookies.get(
        settings.admin_session_cookie
    )
    if not token:
        return RedirectResponse(url="/login", status_code=303)
    require_admin(token)
    return HTMLResponse(WORKDESK_HTML)