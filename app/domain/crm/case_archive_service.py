from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.crm.crm_service import CRMService
from app.domain.statuses.case_statuses import is_closed_case_status
from app.models.case import Case


class CRMCaseArchiveService:
    """Read archived cases without leaking active cases through archive routes."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_card(self, case_id: int) -> dict[str, Any]:
        case = (
            await self.db.execute(select(Case).where(Case.id == case_id))
        ).scalar_one_or_none()
        if case is None:
            raise LookupError("case not found")
        if not is_closed_case_status(case.status):
            raise LookupError("archived case not found")
        return await CRMService(self.db).case_archive_card(case_id)
