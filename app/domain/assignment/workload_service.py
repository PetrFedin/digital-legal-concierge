from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.case import Case
from app.models.lawyer import Lawyer

class WorkloadService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_lawyer_load(self, lawyer_id: int) -> int:
        result = await self.db.execute(select(func.count(Case.id)).where(Case.assigned_lawyer_id == lawyer_id).where(Case.status.notin_(["M1_CLOSED", "M2_CLOSED", "ARCHIVED"])))
        return int(result.scalar_one())

    async def list_active_lawyers(self) -> list[Lawyer]:
        result = await self.db.execute(select(Lawyer).where(Lawyer.is_active.is_(True)).order_by(Lawyer.id.asc()))
        return list(result.scalars().all())
