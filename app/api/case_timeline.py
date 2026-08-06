from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import require_admin
from app.db.session import get_db
from app.domain.cases.case_activity import CaseActivityService
from app.models.case import Case

router = APIRouter(tags=["admin-workdesk"])


@router.get("/admin/workdesk/cases/{case_id}/timeline")
async def workdesk_case_timeline(
    case_id: int,
    before_id: int | None = None,
    limit: int = 6,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    if await db.get(Case, int(case_id)) is None:
        raise HTTPException(status_code=404, detail="Дело не найдено")
    return await CaseActivityService(db).page(
        case_id=int(case_id),
        audience="staff",
        before_id=before_id,
        limit=limit,
    )
