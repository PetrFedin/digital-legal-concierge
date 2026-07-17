from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.models.case import Case
from app.models.lawyer import Lawyer


class CaseAssignmentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_active_lawyers(self) -> list[dict]:
        result = await self.db.execute(
            select(Lawyer).where(Lawyer.is_active.is_(True)).order_by(Lawyer.full_name.asc())
        )
