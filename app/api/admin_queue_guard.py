from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import require_admin
from app.db.session import get_db
from app.domain.cases.assignment_policy import AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES
from app.domain.cases.assignment_service import CaseAssignmentService
from app.models.case import Case
from app.models.lawyer import Lawyer

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


@router.get("/lawyers")
async def safe_legacy_lawyer_list(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Expose only staff who have both a Lawyer profile and a live lawyer login.

    This route intentionally precedes the historical /admin/lawyers handler in
    app.main. Old admin clients may continue reading it, but they can no longer
    see an orphan business profile as an assignable employee.
    """

    require_admin(x_admin_token)
    operational = await CaseAssignmentService(db).list_active_lawyers()
    ids = [int(item["id"]) for item in operational]
    profiles: dict[int, Lawyer] = {}
    if ids:
        rows = list(
            (
                await db.execute(select(Lawyer).where(Lawyer.id.in_(ids)))
            ).scalars().all()
        )
        profiles = {int(item.id): item for item in rows}

    return [
        {
            **item,
            "email": profiles[int(item["id"])].email if int(item["id"]) in profiles else None,
            "is_active": True,
            "login_ready": True,
        }
        for item in operational
    ]


@router.post("/lawyers")
async def retire_legacy_lawyer_creation(
    x_admin_token: str | None = Header(default=None),
):
    """Fail closed instead of manufacturing an assignee without a login account."""

    require_admin(x_admin_token)
    raise HTTPException(
        status_code=409,
        detail={
            "message": (
                "Создание отдельного профиля юриста отключено: такой профиль может получить дело, "
                "но не иметь персонального входа. Создайте сотрудника с ролью lawyer через "
                "Управление доступом — профиль юриста синхронизируется автоматически."
            ),
            "canonical_path": "/access/ui",
            "required_role": "superadmin",
            "mfa_required": True,
        },
    )
