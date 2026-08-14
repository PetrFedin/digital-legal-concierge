from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.cases.assignment_service import CaseAssignmentService
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import resolve_document_actor


router = APIRouter(prefix="/admin/case-assignment", tags=["admin", "case-assignment"])


async def _admin(request: Request, db: AsyncSession, header_token: str | None):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    actor = await resolve_document_actor(db, token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


def _comment(payload: dict, default: str, *, minimum: int = 0) -> str:
    value = str(payload.get("comment") or "").strip() or default
    if len(value) < minimum:
        raise HTTPException(
            status_code=400,
            detail=f"Укажите основание действия минимум в {minimum} символах",
        )
    return value


@router.get("/lawyers")
async def list_lawyer_workload(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _admin(request, db, x_admin_token)
    return await CaseAssignmentService(db).list_active_lawyers()


@router.post("/cases/{case_id}/assign/{lawyer_id}")
async def assign_case(
    case_id: int,
    lawyer_id: int,
    request: Request,
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _admin(request, db, x_admin_token)
    payload = payload or {}
    allow_overload = bool(payload.get("allow_overload", False))
    if allow_overload and actor.role != ROLE_SUPERADMIN:
        raise HTTPException(
            status_code=403,
            detail="Превышение лимита загрузки может подтвердить только суперадминистратор",
        )
    comment = _comment(
        payload,
        "Администратор назначил ответственного юриста",
        minimum=10 if allow_overload else 0,
    )

    assignment_kwargs: dict[str, object] = {}
    if "expected_lawyer_id" in payload:
        assignment_kwargs["expected_lawyer_id"] = payload.get("expected_lawyer_id")
    if payload.get("expected_status") is not None:
        assignment_kwargs["expected_status"] = str(payload.get("expected_status"))

    try:
        case = await CaseAssignmentService(db).assign_case(
            case_id=case_id,
            lawyer_id=lawyer_id,
            actor_type="admin",
            actor_id=int(actor.account_id),
            comment=comment,
            allow_overload=allow_overload,
            **assignment_kwargs,
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise

    return {
        "ok": True,
        "case_id": case.id,
        "assigned_lawyer_id": case.assigned_lawyer_id,
        "actor_id": int(actor.account_id),
    }


@router.post("/cases/{case_id}/unassign")
async def unassign_case(
    case_id: int,
    request: Request,
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _admin(request, db, x_admin_token)
    payload = payload or {}
    comment = _comment(
        payload,
        "Администратор снял назначение юриста",
    )
    try:
        case = await CaseAssignmentService(db).unassign_case(
            case_id=case_id,
            actor_type="admin",
            actor_id=int(actor.account_id),
            comment=comment,
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise

    return {
        "ok": True,
        "case_id": case.id,
        "assigned_lawyer_id": case.assigned_lawyer_id,
        "actor_id": int(actor.account_id),
    }
