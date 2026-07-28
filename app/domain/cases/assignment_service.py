from __future__ import annotations

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_service import CaseService
from app.models.case import Case
from app.models.lawyer import Lawyer


class CaseAssignmentService:
    """Workload-aware facade around the canonical case assignment service.

    Read models for admin, CRM and automatic assignment are built here. The
    actual state mutation, row locking, capacity enforcement, integrity checks
    and audit event are owned exclusively by ``CaseService.assign_lawyer``.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_active_lawyers(self) -> list[dict]:
        active_case_count = func.count(Case.id).label("active_case_count")
        result = await self.db.execute(
            select(
                Lawyer.id,
                Lawyer.full_name,
                Lawyer.phone,
                Lawyer.email,
                Lawyer.specialization,
                Lawyer.workload_limit,
                active_case_count,
            )
            .outerjoin(
                Case,
                and_(
                    Case.assigned_lawyer_id == Lawyer.id,
                    Case.status.notin_(CaseService.CLOSED_STATUSES),
                ),
            )
            .where(Lawyer.is_active.is_(True))
            .group_by(
                Lawyer.id,
                Lawyer.full_name,
                Lawyer.phone,
                Lawyer.email,
                Lawyer.specialization,
                Lawyer.workload_limit,
            )
            .order_by(active_case_count.asc(), Lawyer.full_name.asc(), Lawyer.id.asc())
        )

        lawyers: list[dict] = []
        for row in result.mappings().all():
            workload_limit = max(int(row["workload_limit"] or 0), 0)
            current_workload = int(row["active_case_count"] or 0)
            available_capacity = max(workload_limit - current_workload, 0)
            lawyers.append(
                {
                    "id": row["id"],
                    "full_name": row["full_name"],
                    "phone": row["phone"],
                    "email": row["email"],
                    "specialization": row["specialization"],
                    "workload_limit": workload_limit,
                    "current_workload": current_workload,
                    "available_capacity": available_capacity,
                    "is_available": available_capacity > 0,
                }
            )
        return lawyers

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
