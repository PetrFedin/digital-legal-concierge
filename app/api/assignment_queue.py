from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import require_admin
from app.db.session import get_db
from app.domain.cases.assignment_policy import AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES
from app.domain.cases.case_timeline import get_client_visible_status
from app.models.case import Case

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
        {
            "id": case.id,
            "number": case.case_number,
            "route": case.route,
            "route_label": _ROUTE_LABELS.get(
                str(case.route or ""),
                "Юридическое обращение",
            ),
            "status": case.status,
            "status_label": get_client_visible_status(case.status),
            "lawyer_id": None,
            "lawyer_name": None,
            "next_action": "Назначить ответственного юриста",
            "sla_status": case.sla_status,
            "sla_label": _SLA_LABELS.get(
                str(case.sla_status or ""),
                "SLA не определён",
            ),
            "sla_due_at": case.sla_due_at.isoformat() if case.sla_due_at else None,
            "created_at": case.created_at.isoformat(),
            "queue": "unassigned",
        }
        for case in cases
    ]
    return {
        "queue": "unassigned",
        "count": len(items),
        "items": items,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
