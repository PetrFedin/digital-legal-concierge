from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.models.case import Case
from app.models.lawyer import Lawyer


CLOSED_CASE_STATUSES = {
    "CLOSED",
    "ARCHIVED",
    "CANCELLED",
    "COMPLETED",
    "M1_CLOSED",
    "M2_CLOSED",
}


class CaseAssignmentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_active_lawyers(self) -> list[dict]:
        workload = (
            select(
                Case.assigned_lawyer_id.label("lawyer_id"),
                func.count(Case.id).label("active_cases"),
            )
            .where(
                Case.assigned_lawyer_id.is_not(None),
                func.upper(Case.status).not_in(CLOSED_CASE_STATUSES),
            )
            .group_by(Case.assigned_lawyer_id)
            .subquery()
        )

        result = await self.db.execute(
            select(Lawyer, func.coalesce(workload.c.active_cases, 0))
            .outerjoin(workload, workload.c.lawyer_id == Lawyer.id)
            .where(Lawyer.is_active.is_(True))
            .order_by(Lawyer.full_name.asc())
        )

        lawyers: list[dict] = []
        for lawyer, active_cases in result.all():
            active_cases = int(active_cases)
            lawyers.append(
                {
                    "id": lawyer.id,
                    "full_name": lawyer.full_name,
                    "specialization": lawyer.specialization,
                    "workload_limit": lawyer.workload_limit,
                    "active_cases": active_cases,
                    "available_slots": max(lawyer.workload_limit - active_cases, 0),
                    "is_available": active_cases < lawyer.workload_limit,
                }
            )
        return lawyers

    async def assign_case(
        self,
        *,
        case_id: int,
        lawyer_id: int,
        actor_type: str,
        actor_id: int | None,
        comment: str | None = None,
        allow_overload: bool = False,
    ) -> Case:
        case = await self._get_case(case_id, for_update=True)
        lawyer = await self._get_lawyer(lawyer_id)

        if not lawyer.is_active:
            raise ValueError("Нельзя назначить неактивного юриста")

        if case.assigned_lawyer_id == lawyer.id:
            return case

        active_cases = await self._count_active_cases(lawyer.id)
        if active_cases >= lawyer.workload_limit and not allow_overload:
            raise ValueError("У юриста достигнут лимит активных дел")

        previous_lawyer_id = case.assigned_lawyer_id
        case.assigned_lawyer_id = lawyer.id
        await self.db.flush()

        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="case_lawyer_assigned" if previous_lawyer_id is None else "case_lawyer_reassigned",
            old_value={"assigned_lawyer_id": previous_lawyer_id},
            new_value={"assigned_lawyer_id": lawyer.id},
            comment=comment,
        )
        return case

    async def unassign_case(
        self,
        *,
        case_id: int,
        actor_type: str,
        actor_id: int | None,
        comment: str | None = None,
    ) -> Case:
        case = await self._get_case(case_id, for_update=True)
        previous_lawyer_id = case.assigned_lawyer_id
        if previous_lawyer_id is None:
            return case

        case.assigned_lawyer_id = None
        await self.db.flush()
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="case_lawyer_unassigned",
            old_value={"assigned_lawyer_id": previous_lawyer_id},
            new_value={"assigned_lawyer_id": None},
            comment=comment,
        )
        return case

    async def _get_case(self, case_id: int, *, for_update: bool = False) -> Case:
        query = select(Case).where(Case.id == case_id)
        if for_update:
            query = query.with_for_update()
        case = (await self.db.execute(query)).scalar_one_or_none()
        if case is None:
            raise LookupError("Дело не найдено")
        return case

    async def _get_lawyer(self, lawyer_id: int) -> Lawyer:
        lawyer = (
            await self.db.execute(select(Lawyer).where(Lawyer.id == lawyer_id))
        ).scalar_one_or_none()
        if lawyer is None:
            raise LookupError("Юрист не найден")
        return lawyer

    async def _count_active_cases(self, lawyer_id: int) -> int:
        result = await self.db.execute(
            select(func.count(Case.id)).where(
                Case.assigned_lawyer_id == lawyer_id,
                func.upper(Case.status).not_in(CLOSED_CASE_STATUSES),
            )
        )
        return int(result.scalar_one())
