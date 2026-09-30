from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.case_assignment_repair import router as case_assignment_repair_router
from app.api.m1_internal_payment_recovery import router as m1_internal_payment_recovery_router
from app.api.m2_payment_reservation_repair import router as m2_payment_reservation_repair_router
from app.config import settings
from app.db.session import get_db
from app.domain.cases.assignment_service import CaseAssignmentService
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import resolve_document_actor


# Assignment owns assignment and the still-local recovery endpoints only. Staff
# UI guards, refunds and Workdesk integrity have canonical/product owners and
# must not be re-mounted here merely to win FastAPI include order.
router = APIRouter(tags=["admin", "case-assignment"])
assignment_router = APIRouter(prefix="/admin/case-assignment")
router.include_router(m1_internal_payment_recovery_router)
router.include_router(m2_payment_reservation_repair_router)
router.include_router(case_assignment_repair_router)


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


def _required_assignment_snapshot(
    payload: dict,
    *,
    require_assigned_lawyer: bool,
) -> tuple[int | None, str]:
    if "expected_lawyer_id" not in payload:
        raise HTTPException(
            status_code=409,
            detail="Экран назначения устарел: обновите текущего ответственного",
        )
    raw_lawyer = payload.get("expected_lawyer_id")
    if raw_lawyer in (None, ""):
        expected_lawyer_id = None
    else:
        try:
            expected_lawyer_id = int(raw_lawyer)
        except (TypeError, ValueError) as error:
            raise HTTPException(
                status_code=409,
                detail="Некорректный снимок текущего ответственного",
            ) from error
        if expected_lawyer_id <= 0:
            raise HTTPException(
                status_code=409,
                detail="Некорректный снимок текущего ответственного",
            )
    if require_assigned_lawyer and expected_lawyer_id is None:
        raise HTTPException(
            status_code=409,
            detail="Для снятия назначения нужен актуальный ответственный юрист",
        )

    expected_status = str(payload.get("expected_status") or "").strip()
    if not expected_status:
        raise HTTPException(
            status_code=409,
            detail="Экран назначения устарел: отсутствует текущий статус дела",
        )
    return expected_lawyer_id, expected_status


@assignment_router.get("/lawyers")
async def list_lawyer_workload(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _admin(request, db, x_admin_token)
    return await CaseAssignmentService(db).list_active_lawyers()


@assignment_router.post("/cases/{case_id}/assign/{lawyer_id}")
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

    expected_lawyer_id, expected_status = _required_assignment_snapshot(
        payload,
        require_assigned_lawyer=False,
    )

    try:
        case = await CaseAssignmentService(db).assign_case(
            case_id=case_id,
            lawyer_id=lawyer_id,
            actor_type="admin",
            actor_id=int(actor.account_id),
            comment=comment,
            allow_overload=allow_overload,
            expected_lawyer_id=expected_lawyer_id,
            expected_status=expected_status,
        )
        response = {
            "ok": True,
            "case_id": int(case.id),
            "assigned_lawyer_id": (
                int(case.assigned_lawyer_id)
                if case.assigned_lawyer_id is not None
                else None
            ),
            "actor_id": int(actor.account_id),
        }
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

    return response


@assignment_router.post("/cases/{case_id}/unassign")
async def unassign_case(
    case_id: int,
    request: Request,
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _admin(request, db, x_admin_token)
    payload = payload or {}
    comment = _comment(payload, "Администратор снял назначение юриста")
    expected_lawyer_id, expected_status = _required_assignment_snapshot(
        payload,
        require_assigned_lawyer=True,
    )
    try:
        case = await CaseAssignmentService(db).unassign_case(
            case_id=case_id,
            actor_type="admin",
            actor_id=int(actor.account_id),
            comment=comment,
            expected_lawyer_id=expected_lawyer_id,
            expected_status=expected_status,
        )
        response = {
            "ok": True,
            "case_id": int(case.id),
            "assigned_lawyer_id": (
                int(case.assigned_lawyer_id)
                if case.assigned_lawyer_id is not None
                else None
            ),
            "actor_id": int(actor.account_id),
        }
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

    return response


router.include_router(assignment_router)
