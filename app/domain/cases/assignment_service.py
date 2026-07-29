from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.sla_service import CaseSLAService
from app.domain.notifications.notification_engine import NotificationEngine
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
        self.notifications = NotificationEngine(db)
        self.sla = CaseSLAService(db)

    async def list_active_lawyers(self) -> list[dict]:
        workload = (
            select(
                Case.assigned_lawyer_id.label("lawyer_id"),
                func.count(Case.id).label("active_cases"),
            )
            .where(
                Case.assigned_lawyer_id.is_not(None),
                Case.status.notin_(tuple(CLOSED_CASE_STATUSES)),
            )
            .group_by(Case.assigned_lawyer_id)
            .subquery()
        )

        result = await self.db.execute(
            select(Lawyer, func.coalesce(workload.c.active_cases, 0))
            .outerjoin(workload, workload.c.lawyer_id == Lawyer.id)
            .where(Lawyer.is_active.is_(True))
            .order_by(Lawyer.full_name.asc(), Lawyer.id.asc())
        )

        lawyers: list[dict] = []
        for lawyer, active_cases in result.all():
            active_cases = int(active_cases)
            workload_limit = max(int(lawyer.workload_limit or 0), 0)
            lawyers.append(
                {
                    "id": lawyer.id,
                    "full_name": lawyer.full_name,
                    "specialization": lawyer.specialization,
                    "workload_limit": workload_limit,
                    "active_cases": active_cases,
                    "available_slots": max(workload_limit - active_cases, 0),
                    "load_ratio": (
                        active_cases / workload_limit if workload_limit else 1.0
                    ),
                    "is_available": (
                        workload_limit > 0 and active_cases < workload_limit
                    ),
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
        active_cases = await self._count_active_cases(lawyer.id)
        return await self._assign_case_model(
            case=case,
            lawyer=lawyer,
            active_cases=active_cases,
            actor_type=actor_type,
            actor_id=actor_id,
            comment=comment,
            allow_overload=allow_overload,
        )

    async def auto_assign_case(
        self,
        *,
        case_id: int,
        actor_type: str = "system",
        actor_id: int | None = None,
        comment: str | None = "Автоматическое назначение по текущей загрузке",
    ) -> Case | None:
        case = await self._get_case(case_id, for_update=True)
        if case.assigned_lawyer_id is not None:
            return case
        self._ensure_case_can_be_assigned(case)

        lawyer_rows = await self.list_active_lawyers()
        best = self._choose_best_lawyer(lawyer_rows)
        if best is None:
            return None

        lawyer = await self._get_lawyer(best["id"])
        return await self._assign_case_model(
            case=case,
            lawyer=lawyer,
            active_cases=best["active_cases"],
            actor_type=actor_type,
            actor_id=actor_id,
            comment=comment,
            allow_overload=False,
        )

    async def assign_queue(
        self,
        *,
        limit: int = 100,
        actor_type: str = "system",
        actor_id: int | None = None,
    ) -> dict:
        safe_limit = min(max(int(limit), 1), 500)
        result = await self.db.execute(
            select(Case)
            .where(
                Case.assigned_lawyer_id.is_(None),
                Case.status.notin_(tuple(CLOSED_CASE_STATUSES)),
            )
            .order_by(Case.created_at.asc(), Case.id.asc())
            .limit(safe_limit)
            .with_for_update(skip_locked=True)
        )
        cases = list(result.scalars().all())
        lawyer_rows = await self.list_active_lawyers()

        assigned: list[dict] = []
        for case in cases:
            best = self._choose_best_lawyer(lawyer_rows)
            if best is None:
                break
            lawyer = await self._get_lawyer(best["id"])
            await self._assign_case_model(
                case=case,
                lawyer=lawyer,
                active_cases=best["active_cases"],
                actor_type=actor_type,
                actor_id=actor_id,
                comment="Автоматическое распределение очереди",
                allow_overload=False,
            )
            best["active_cases"] += 1
            best["available_slots"] = max(
                best["workload_limit"] - best["active_cases"],
                0,
            )
            best["load_ratio"] = (
                best["active_cases"] / best["workload_limit"]
                if best["workload_limit"]
                else 1.0
            )
            best["is_available"] = (
                best["active_cases"] < best["workload_limit"]
            )
            assigned.append(
                {
                    "case_id": case.id,
                    "case_number": case.case_number,
                    "lawyer_id": lawyer.id,
                    "lawyer": lawyer.full_name,
                }
            )

        return {
            "examined": len(cases),
            "assigned_count": len(assigned),
            "unassigned_count": len(cases) - len(assigned),
            "assigned": assigned,
            "capacity_exhausted": (
                bool(cases) and len(assigned) < len(cases)
            ),
        }

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

        await self.sla.clear_assignment_sla(
            case=case,
            actor_type=actor_type,
            actor_id=actor_id,
            comment=comment,
        )
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
        await self.db.flush()
        return case

    async def _assign_case_model(
        self,
        *,
        case: Case,
        lawyer: Lawyer,
        active_cases: int,
        actor_type: str,
        actor_id: int | None,
        comment: str | None,
        allow_overload: bool,
    ) -> Case:
        self._ensure_case_can_be_assigned(case)
        if not lawyer.is_active:
            raise ValueError("Нельзя назначить неактивного юриста")

        workload_limit = max(int(lawyer.workload_limit or 0), 0)
        if workload_limit == 0 and not allow_overload:
            raise ValueError("У юриста отсутствует доступная ёмкость")
        if active_cases >= workload_limit and not allow_overload:
            raise ValueError("У юриста достигнут лимит активных дел")
        if case.assigned_lawyer_id == lawyer.id:
            if case.sla_status in {None, "", "NOT_STARTED"}:
                await self.sla.start_assignment_sla(
                    case=case,
                    lawyer_id=lawyer.id,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    comment=comment,
                )
            return case

        previous_lawyer_id = case.assigned_lawyer_id
        await self.sla.start_assignment_sla(
            case=case,
            lawyer_id=lawyer.id,
            actor_type=actor_type,
            actor_id=actor_id,
            comment=comment,
        )

        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action=(
                "case_lawyer_assigned"
                if previous_lawyer_id is None
                else "case_lawyer_reassigned"
            ),
            old_value={"assigned_lawyer_id": previous_lawyer_id},
            new_value={"assigned_lawyer_id": lawyer.id},
            comment=comment,
        )
        await self.notifications.emit(
            event_code="LAWYER_ASSIGNED",
            case_id=case.id,
            payload={"case_number": case.case_number},
            dedupe_key=(
                f"case:{case.id}:lawyer-assigned:{lawyer.id}:"
                f"{int(case.assigned_at.timestamp()) if case.assigned_at else 0}"
            ),
        )
        await self.db.flush()
        return case

    @staticmethod
    def _choose_best_lawyer(lawyers: list[dict]) -> dict | None:
        available = [lawyer for lawyer in lawyers if lawyer["is_available"]]
        if not available:
            return None
        return min(
            available,
            key=lambda lawyer: (
                lawyer["load_ratio"],
                lawyer["active_cases"],
                lawyer["id"],
            ),
        )

    @staticmethod
    def _ensure_case_can_be_assigned(case: Case) -> None:
        if str(case.status).upper() in CLOSED_CASE_STATUSES:
            raise ValueError("Нельзя назначить юриста на закрытое дело")

    async def _get_case(
        self,
        case_id: int,
        *,
        for_update: bool = False,
    ) -> Case:
        query = select(Case).where(Case.id == case_id)
        if for_update:
            query = query.with_for_update()
        case = (await self.db.execute(query)).scalar_one_or_none()
        if case is None:
            raise LookupError("Дело не найдено")
        return case

    async def _get_lawyer(self, lawyer_id: int) -> Lawyer:
        lawyer = (
            await self.db.execute(
                select(Lawyer).where(Lawyer.id == lawyer_id)
            )
        ).scalar_one_or_none()
        if lawyer is None:
            raise LookupError("Юрист не найден")
        return lawyer

    async def _count_active_cases(self, lawyer_id: int) -> int:
        result = await self.db.execute(
            select(func.count(Case.id)).where(
                Case.assigned_lawyer_id == lawyer_id,
                Case.status.notin_(tuple(CLOSED_CASE_STATUSES)),
            )
        )
        return int(result.scalar_one())
