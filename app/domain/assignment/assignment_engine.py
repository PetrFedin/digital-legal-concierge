from sqlalchemy.ext.asyncio import AsyncSession
from app.domain.assignment.workload_service import WorkloadService
from app.domain.cases.case_service import CaseService
from app.models.case import Case
from app.models.lawyer import Lawyer

class AssignmentEngine:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.workload = WorkloadService(db)
        self.case_service = CaseService(db)

    async def assign_best_lawyer(self, *, case: Case, actor_id: int | None = None) -> Lawyer | None:
        lawyers = await self.workload.list_active_lawyers()
        best_lawyer = None
        best_load = None
        for lawyer in lawyers:
            load = await self.workload.get_lawyer_load(lawyer.id)
            if load >= lawyer.workload_limit:
                continue
            if best_load is None or load < best_load:
                best_lawyer = lawyer
                best_load = load
        if not best_lawyer:
            return None
        await self.case_service.assign_lawyer(case=case, lawyer_id=best_lawyer.id, actor_id=actor_id or 0)
        return best_lawyer
