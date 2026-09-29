from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import require_admin
from app.api.workdesk_renderer import render_workdesk_html
from app.config import settings
from app.db.session import get_db
from app.domain.cases.assignment_policy import AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES
from app.domain.cases.case_timeline import get_client_visible_status
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.lawyer import Lawyer
from app.presentation_time import to_business_timezone
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["admin-assignment-queue"])

_ROUTE_LABELS = {
    "M1": "Ведение дела",
    "M2": "Консультация",
}
_SLA_LABELS = {
    "NOT_STARTED": "SLA не запущен",
    "FIRST_RESPONSE_PENDING": "Ожидается первая реакция",
    "FIRST_RESPONSE_OK": "Первая реакция в срок",
    "FIRST_RESPONSE_OVERDUE": "Просрочена первая реакция",
    "ACTION_PENDING": "Ожидается действие",
    "ACTION_OK": "Действие выполнено в срок",
    "ACTION_OVERDUE": "Действие просрочено",
    "PAUSED": "SLA приостановлен",
    "CLOSED": "SLA завершён",
}
_ACTIVE_CONSULTATION_STATUSES = ("BOOKED", "CONFIRMED")
_CLOSED_CASE_STATUSES = (
    "M1_CLOSED",
    "M1_SELF_FILING_CLOSED",
    "M2_CLOSED",
    "ARCHIVED",
)


def _case_queue_payload(
    case: Case,
    *,
    queue: str,
    lawyer_id: int | None,
    lawyer_name: str | None,
    next_action: str,
) -> dict[str, object]:
    return {
        "id": case.id,
        "number": case.case_number,
        "route": case.route,
        "route_label": _ROUTE_LABELS.get(
            str(case.route or ""),
            "Юридическое обращение",
        ),
        "status": case.status,
        "status_label": get_client_visible_status(case.status),
        "lawyer_id": lawyer_id,
        "lawyer_name": lawyer_name,
        "next_action": next_action,
        "sla_status": case.sla_status,
        "sla_label": _SLA_LABELS.get(
            str(case.sla_status or ""),
            "SLA не определён",
        ),
        "sla_due_at": case.sla_due_at.isoformat() if case.sla_due_at else None,
        "created_at": case.created_at.isoformat(),
        "queue": queue,
    }


@router.get("/admin/workdesk/ui", response_class=HTMLResponse)
async def guided_workdesk_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Render the canonical Workdesk only for a live personal admin session."""

    token = x_admin_token or request.cookies.get(settings.admin_session_cookie)
    if not token:
        return RedirectResponse(url="/login", status_code=303)
    try:
        actor = await resolve_document_actor(db, token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        raise
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return HTMLResponse(render_workdesk_html())


@router.get("/admin/workdesk/cases/{case_id}/responsibility")
async def workdesk_case_responsibility(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Explain who is operationally responsible without inventing M2 assignment."""

    require_admin(x_admin_token)
    case = await db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Дело не найдено")

    if str(case.route or "") == "M2":
        row = (
            await db.execute(
                select(Consultation, Lawyer)
                .outerjoin(Lawyer, Lawyer.id == Consultation.lawyer_id)
                .where(Consultation.case_id == case.id)
                .order_by(Consultation.created_at.desc(), Consultation.id.desc())
                .limit(1)
            )
        ).one_or_none()
        consultation, lawyer = row if row is not None else (None, None)
        return {
            "case_id": case.id,
            "route": "M2",
            "mode": "consultation_slot",
            "lawyer_id": lawyer.id if lawyer else None,
            "lawyer_name": lawyer.full_name if lawyer else None,
            "consultation_id": consultation.id if consultation else None,
            "consultation_status": consultation.status if consultation else None,
            "scheduled_at": (
                consultation.scheduled_at.isoformat()
                if consultation and consultation.scheduled_at
                else None
            ),
        }

    lawyer = (
        await db.get(Lawyer, case.assigned_lawyer_id)
        if case.assigned_lawyer_id
        else None
    )
    return {
        "case_id": case.id,
        "route": str(case.route or ""),
        "mode": "case_assignment",
        "lawyer_id": lawyer.id if lawyer else None,
        "lawyer_name": lawyer.full_name if lawyer else None,
        "consultation_id": None,
        "consultation_status": None,
        "scheduled_at": None,
    }


@router.get("/admin/work-queues/unassigned")
async def actionable_unassigned_queue(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Return only cases where automatic assignment is genuinely due.

    The generic dynamic queue historically treated every active case without a
    Case.assigned_lawyer_id as operationally unassigned. That included a client
    still using the calculator and M2 consultations whose lawyer is selected by
    the calendar slot. This static route is mounted before the legacy dynamic
    route so both the guided workdesk and old admin UI receive the safe queue.
    """

    require_admin(x_admin_token)
    cases = list(
        (
            await db.execute(
                select(Case)
                .where(
                    Case.assigned_lawyer_id.is_(None),
                    Case.status.in_(AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES),
                )
                .order_by(Case.created_at.asc(), Case.id.asc())
                .limit(200)
            )
        ).scalars().all()
    )
    items = [
        _case_queue_payload(
            case,
            queue="unassigned",
            lawyer_id=None,
            lawyer_name=None,
            next_action="Назначить ответственного юриста",
        )
        for case in cases
    ]
    return {
        "queue": "unassigned",
        "count": len(items),
        "items": items,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/admin/work-queues/consultations")
async def consultation_queue_with_slot_lawyer(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Project the actual booked lawyer for the current business day.

    In M2 the lawyer is selected by the consultation slot. A Case may therefore
    intentionally have no assigned_lawyer_id, and presenting it as "без юриста"
    is misleading. The queue is also a business-day view: UTC persistence must
    not move an early-morning or late-evening local appointment into the wrong
    admin queue.
    """

    require_admin(x_admin_token)
    now = datetime.now(timezone.utc)
    business_now = to_business_timezone(now)
    business_zone = business_now.tzinfo
    business_date = business_now.date()
    today_start = datetime.combine(business_date, time.min, tzinfo=business_zone).astimezone(
        timezone.utc
    )
    tomorrow_start = datetime.combine(
        business_date + timedelta(days=1),
        time.min,
        tzinfo=business_zone,
    ).astimezone(timezone.utc)
    rows = (
        await db.execute(
            select(Case, Consultation, Lawyer)
            .join(Consultation, Consultation.case_id == Case.id)
            .join(Lawyer, Lawyer.id == Consultation.lawyer_id)
            .where(Case.status.notin_(_CLOSED_CASE_STATUSES))
            .where(Consultation.status.in_(_ACTIVE_CONSULTATION_STATUSES))
            .where(Consultation.scheduled_at >= today_start)
            .where(Consultation.scheduled_at < tomorrow_start)
            .order_by(Consultation.scheduled_at.asc(), Consultation.id.asc())
            .limit(200)
        )
    ).all()

    items: list[dict[str, object]] = []
    seen_cases: set[int] = set()
    for case, consultation, lawyer in rows:
        if case.id in seen_cases:
            continue
        seen_cases.add(case.id)
        scheduled_at = consultation.scheduled_at
        action = (
            "Провести консультацию и зафиксировать итог"
            if scheduled_at is not None and scheduled_at <= now
            else "Подготовиться к назначенной консультации"
        )
        item = _case_queue_payload(
            case,
            queue="consultations",
            lawyer_id=lawyer.id,
            lawyer_name=lawyer.full_name,
            next_action=action,
        )
        item["consultation_id"] = consultation.id
        item["consultation_at"] = (
            scheduled_at.isoformat() if scheduled_at is not None else None
        )
        items.append(item)

    return {
        "queue": "consultations",
        "count": len(items),
        "items": items,
        "generated_at": now.isoformat(),
        "business_date": business_date.isoformat(),
        "business_timezone": settings.business_timezone,
        "business_timezone_label": settings.business_timezone_label,
    }
