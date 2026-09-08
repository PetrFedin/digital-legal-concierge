from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import require_admin
from app.config import settings
from app.db.session import get_db
from app.domain.cases.assignment_policy import AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES
from app.domain.cases.assignment_service import CaseAssignmentService
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.scheduler.scheduler import AppScheduler
from app.security.access_control import ROLE_SUPERADMIN
from app.security.document_access import resolve_document_actor

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


@router.post("/scheduler/run-once")
async def safe_manual_scheduler_run_once(
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Run the broad operational scheduler only as an explicit privileged action."""

    token = x_admin_token or request.cookies.get(settings.admin_session_cookie)
    actor = await resolve_document_actor(db, token)
    if actor.role != ROLE_SUPERADMIN:
        raise HTTPException(
            status_code=403,
            detail="Ручной запуск полного scheduler доступен только суперадминистратору с MFA",
        )
    if str(payload.get("confirmation") or "").strip() != "RUN_SCHEDULER_ONCE":
        raise HTTPException(
            status_code=400,
            detail=(
                "Для ручного массового запуска передайте confirmation=RUN_SCHEDULER_ONCE. "
                "Обычная работа должна выполняться фоновым scheduler автоматически."
            ),
        )

    db.add(
        AuditLog(
            actor_type="admin_user",
            actor_id=actor.account_id,
            action="scheduler.manual_run_requested",
            entity_type="scheduler",
            entity_id=None,
            old_value=None,
            new_value={"mode": "full_cycle", "source": "manual_admin_api"},
            comment="Суперадминистратор с MFA подтвердил ручной запуск полного scheduler",
        )
    )
    await db.commit()

    result = await AppScheduler().run_once()
    compact_result = {
        "scheduler_acquired": bool(result.get("scheduler_acquired")),
        "scheduler_ok": bool(result.get("scheduler_ok")),
        "job_names": sorted(
            str(key)
            for key in result.keys()
            if key not in {"scheduler_acquired", "scheduler_ok", "scheduler_skipped"}
        ),
        "skipped": result.get("scheduler_skipped"),
    }
    db.add(
        AuditLog(
            actor_type="admin_user",
            actor_id=actor.account_id,
            action="scheduler.manual_run_completed",
            entity_type="scheduler",
            entity_id=None,
            old_value=None,
            new_value=compact_result,
            comment=(
                "Ручной полный scheduler завершён"
                if compact_result["scheduler_ok"]
                else "Ручной полный scheduler завершён с ошибкой/пропуском"
            ),
        )
    )
    await db.commit()
    return result
