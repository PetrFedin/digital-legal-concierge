from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_service import CaseService
from app.domain.cases.lawyer_capacity_service import LawyerCapacityService
from app.models.case import Case


class CaseAssignmentService:
    """Workload-aware facade around the canonical case assignment service.

    Read models count both assigned cases and pre-assignment consultation
    reservations. The actual mutation remains owned by ``CaseService``.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_active_lawyers(self) -> list[dict]:
        rows = await LawyerCapacityService(self.db).list_active_lawyers()
        return [
            {
                "id": lawyer.id,
                "full_name": lawyer.full_name,
                "phone": lawyer.phone,
                "email": lawyer.email,
                "specialization": lawyer.specialization,
                "workload_limit": snapshot.workload_limit,
                "current_workload": snapshot.current_workload,
                "available_capacity": snapshot.available_capacity,
                "is_available": snapshot.is_available,
            }
            for lawyer, snapshot in rows
        ]

    async def assign_lawyer(
        self,
        *,
        case: Case,
        lawyer_id: int,
        actor_id: int | None,
    ) -> Case:
        return await CaseService(self.db).assign_lawyer(
            case=case,
            lawyer_id=lawyer_id,
            actor_id=actor_id,
        )
