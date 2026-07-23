from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import require_admin, require_crm_reader
from app.db.session import get_db
from app.domain.crm.case_archive_service import CRMCaseArchiveService
from app.domain.crm.client_archive_service import CRMClientArchiveService
from app.domain.crm.crm_service import CRMService


router = APIRouter(prefix="/admin/crm", tags=["admin-crm"])


class ArchiveClientRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


async def _set_client_archive_state(
    *,
    db: AsyncSession,
    user_id: int,
    archived: bool,
    actor: dict,
    reason: str | None,
) -> dict:
    try:
        result = await CRMClientArchiveService(db).set_state(
            user_id=user_id,
            archived=archived,
            actor_id=actor.get("uid"),
            reason=reason,
        )
        await db.commit()
        return result
    except LookupError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        raise


@router.get("/clients")
async def list_clients(
    q: str | None = Query(default=None, max_length=255),
    archive: str = Query(default="active", pattern="^(active|archived|all)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_crm_reader),
):
    try:
        return await CRMService(db).list_clients(
            query=q,
            archive=archive,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/clients/{user_id}")
async def client_card(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_crm_reader),
):
    try:
        return await CRMService(db).client_card(user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/clients/{user_id}/timeline")
async def client_timeline(
    user_id: int,
    limit: int = Query(default=300, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_crm_reader),
):
    try:
        return {
            "user_id": user_id,
            "items": await CRMService(db).client_timeline(user_id, limit=limit),
        }
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/clients/{user_id}/archive")
async def archive_client(
    user_id: int,
    body: ArchiveClientRequest,
    db: AsyncSession = Depends(get_db),
    actor: dict = Depends(require_admin),
):
    return await _set_client_archive_state(
        db=db,
        user_id=user_id,
        archived=True,
        actor=actor,
        reason=body.reason,
    )


@router.post("/clients/{user_id}/restore")
async def restore_client(
    user_id: int,
    body: ArchiveClientRequest,
    db: AsyncSession = Depends(get_db),
    actor: dict = Depends(require_admin),
):
    return await _set_client_archive_state(
        db=db,
        user_id=user_id,
        archived=False,
        actor=actor,
        reason=body.reason,
    )


@router.get("/cases/archive")
async def archived_cases(
    q: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_crm_reader),
):
    return await CRMService(db).archived_cases(
        query=q,
        limit=limit,
        offset=offset,
    )


@router.get("/cases/archive/{case_id}")
async def archived_case_card(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_crm_reader),
):
    try:
        return await CRMCaseArchiveService(db).get_card(case_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/analytics/overview")
async def analytics_overview(
    days: int = Query(default=30, ge=1, le=3660),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_crm_reader),
):
    return await CRMService(db).analytics_overview(days=days)


@router.get("/analytics/funnel")
async def analytics_funnel(
    days: int = Query(default=30, ge=1, le=3660),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_crm_reader),
):
    return await CRMService(db).funnel(days=days)
