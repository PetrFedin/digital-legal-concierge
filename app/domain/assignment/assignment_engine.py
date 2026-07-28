from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.assignment_service import CaseAssignmentService
from app.models.case import Case
from app.models.lawyer import Lawyer


class AssignmentEngine:
    """Select and assign the least-loaded eligible lawyer.

    Capacity calculation and the final mutation both go through
    ``CaseAssignmentService`` so automatic assignment cannot bypass the same
    integrity and workload rules used by explicit assignment flows.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.assignments = CaseAssignmentService(db)

    async def assign_best_lawyer(
        self,
        *,
        case: Case,
        actor_id: int | None = None,
    ) -> Lawyer | None:
        lawyers = await self.assignments.list_active_lawyers()

        # Re-running auto-assignment for an already assigned case is
        # idempotent. It must not silently replace the current lawyer merely
        # because another lawyer currently has a lower workload.
        if case.assigned_lawyer_id is not None:
            current = next(
                (
                    item
                    for item in lawyers
                    if item["id"] == case.assigned_lawyer_id
                ),
                None,
            )
            if current is None:
                return None
            assigned_case = await self.assignments.assign_lawyer(
                case=case,
                lawyer_id=current["id"],
                actor_id=actor_id,
            )
            return await self.db.get(Lawyer, assigned_case.assigned_lawyer_id)

        best = next((item for item in lawyers if item["is_available"]), None)
        if best is None:
            return None

        assigned_case = await self.assignments.assign_lawyer(
            case=case,
            lawyer_id=best["id"],
            actor_id=actor_id,
        )
        return await self.db.get(Lawyer, assigned_case.assigned_lawyer_id)
