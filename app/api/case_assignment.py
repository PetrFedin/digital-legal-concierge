from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.domain.cases.assignment_service import CaseAssignmentService
from app.security.access_control import verify_access_token


router = APIRouter(prefix="/admin/case-assignment", tags=["admin", "case-assignment"])


def check_admin_token(token: str | None) -> None:
    if not verify_access_token(token):
        raise HTTPException(status_code=401, detail="bad token")


@router.get("/lawyers")
async def list_lawyer_workload(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check_admin_token(x_admin_token)
    return await CaseAssignmentService(db).list_active_lawyers()


@router.post("/cases/{case_id}/assign/{lawyer_id}")
async def assign_case(
    case_id: int,
    lawyer_id: int,
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check_admin_token(x_admin_token)
    payload = payload or {}
    try:
        case = await CaseAssignmentService(db).assign_case(
            case_id=case_id,
            lawyer_id=lawyer_id,
            actor_type="admin",
            actor_id=payload.get("actor_id"),
            comment=payload.get("comment"),
            allow_overload=bool(payload.get("allow_overload", False)),
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error

    return {
        "ok": True,
        "case_id": case.id,
        "assigned_lawyer_id": case.assigned_lawyer_id,
    }


@router.post("/cases/{case_id}/unassign")
async def unassign_case(
    case_id: int,
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check_admin_token(x_admin_token)
    payload = payload or {}
    try:
        case = await CaseAssignmentService(db).unassign_case(
            case_id=case_id,
            actor_type="admin",
            actor_id=payload.get("actor_id"),
            comment=payload.get("comment"),
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error

    return {
        "ok": True,
        "case_id": case.id,
        "assigned_lawyer_id": case.assigned_lawyer_id,
    }
