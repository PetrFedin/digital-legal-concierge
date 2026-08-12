from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import require_admin
from app.db.session import get_db
from app.domain.cases.assignment_policy import AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES
from app.models.case import Case

router = APIRouter(prefix="/admin", tags=["admin-queue-guard"])


@router.get("/queue")
async def safe_legacy_admin_queue(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Compatibility queue containing only cases that genuinely need M1 assignment.

    Older admin clients still call /admin/queue. Keeping the endpoint is useful,
    but treating every active unassigned case as staff work is not: calculator
    intake belongs to the client and M2 responsibility belongs to the selected
    consultation slot. The compatibility projection now follows the same policy
    as the current workdesk and assignment service.
    """

    require_admin(x_admin_token)
    rows = list(
        (
            await db.execute(
                select(Case)
                .where(Case.assigned_lawyer_id.is_(None))
                .where(Case.status.in_(AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES))
                .order_by(Case.created_at.asc(), Case.id.asc())
                .limit(100)
            )
        ).scalars().all()
    )
    return [
        {
            "id": case.id,
            "number": case.case_number,
            "route": case.route,
            "status": case.status,
            "lawyer_id": None,
            "next_action": case.next_action,
            "assignment_required": True,
        }
        for case in rows
    ]
