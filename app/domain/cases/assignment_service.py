from __future__ import annotations

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_service import CaseAssignmentError, CaseService
from app.models.case import Case
from app.models.lawyer import Lawyer


class CaseAssignmentService:
    """Capacity-aware facade for assigning lawyers to cases.

    ``CaseService`` remains the source of truth for assignment integrity and
    audit events. This facade adds workload visibility and prevents new cases
    from being assigned to lawyers who have reached their configured limit.
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
        if case is None:
            raise CaseAssignmentError("Дело для назначения не найдено.")

        lawyer = (
            await self.db.execute(
                select(Lawyer)
                .where(
                    Lawyer.id == lawyer_id,
                    Lawyer.is_active.is_(True),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if lawyer is None:
            raise CaseAssignmentError("Активный юрист для назначения не найден.")

        # Repeating the same assignment is idempotent and must not fail merely
        # because the lawyer is now exactly at the configured capacity.
        if case.assigned_lawyer_id == lawyer_id:
            return await CaseService(self.db).assign_lawyer(
                case=case,
                lawyer_id=lawyer_id,
                actor_id=actor_id,
            )

        workload_limit = max(int(lawyer.workload_limit or 0), 0)
        current_workload = int(
            (
                await self.db.execute(
                    select(func.count(Case.id)).where(
                        Case.assigned_lawyer_id == lawyer_id,
                        Case.status.notin_(CaseService.CLOSED_STATUSES),
                    )
                )
            ).scalar_one()
            or 0
        )
        if current_workload >= workload_limit:
            raise CaseAssignmentError(
                "Нельзя назначить юриста: достигнут лимит активных дел "
                f"({current_workload}/{workload_limit})."
            )

        return await CaseService(self.db).assign_lawyer(
            case=case,
            lawyer_id=lawyer_id,
            actor_id=actor_id,
        )
