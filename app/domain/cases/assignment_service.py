from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.assignment_policy import (
    AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES,
    automatic_assignment_required,
)
from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.sla_service import CaseSLAService
from app.domain.notifications.notification_engine import NotificationEngine
from app.models.admin_user import AdminUser
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.security.access_control import ROLE_LAWYER, normalize_roles


CLOSED_CASE_STATUSES = {
    "CLOSED",
    "ARCHIVED",
    "CANCELLED",
    "COMPLETED",
    "M1_CLOSED",
    "M2_CLOSED",
}
_UNSET = object()


class CaseAssignmentService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.notifications = NotificationEngine(db)
        self.sla = CaseSLAService(db)

    async def _active_lawyer_login_emails(self) -> set[str]:
        """Return staff emails that can actually authenticate as a lawyer.

        Lawyer is the business profile used for workload/ownership, while
        AdminUser is the personal login account. A profile without an active
        AdminUser+lawyer role is not an operational assignee: assigning a case
        to it would create an E2E dead end because nobody could open the lawyer
        cabinet as that person.
        """

        rows = (
            await self.db.execute(
                select(AdminUser.email, AdminUser.role).where(
                    AdminUser.is_active.is_(True),
                    AdminUser.email.is_not(None),
                )
            )
        ).all()
        return {
            str(email).strip().lower()
            for email, roles in rows
            if str(email or "").strip()
            and ROLE_LAWYER in normalize_roles(roles)
        }

    async def _assert_lawyer_login_ready(self, lawyer: Lawyer) -> None:
        email = str(lawyer.email or "").strip().lower()
        login_emails = await self._active_lawyer_login_emails()
        if not email or email not in login_emails:
            raise ValueError(
                "Юрист не имеет активного персонального аккаунта с ролью lawyer. "
                "Создайте/восстановите сотрудника через Управление доступом и только потом назначайте дело"
            )

    @staticmethod
    def _capacity_payload(lawyer: Lawyer, active_cases: int) -> dict:
        active_cases = int(active_cases)
        workload_limit = max(int(lawyer.workload_limit or 0), 0)
        return {
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
            "login_ready": True,
        }

    async def _active_case_counts(self) -> dict[int, int]:
        result = await self.db.execute(
            select(
                Case.assigned_lawyer_id,
                func.count(Case.id),
            )
            .where(
                Case.assigned_lawyer_id.is_not(None),
                Case.status.notin_(tuple(CLOSED_CASE_STATUSES)),
            )
            .group_by(Case.assigned_lawyer_id)
        )
        return {
            int(lawyer_id): int(active_cases)
            for lawyer_id, active_cases in result.all()
            if lawyer_id is not None
        }

    async def _locked_active_lawyers(self) -> list[dict]:
        """Lock assignable Lawyer rows before reading authoritative workload.

        Case row locks protect two admins from racing on the same case. They do
        not protect two different cases from concurrently consuming the last
        capacity slot of the same lawyer. Serializing on Lawyer rows closes that
        cross-case TOCTOU window for manual, automatic and queue assignment.

        Rows are locked in deterministic id order so all assignment paths use a
        consistent Case -> Lawyer lock order.
        """

        login_emails = await self._active_lawyer_login_emails()
        result = await self.db.execute(
            select(Lawyer)
            .where(Lawyer.is_active.is_(True))
            .order_by(Lawyer.id.asc())
            .with_for_update()
        )
        lawyers = list(result.scalars().all())

        # Recalculate only after the lawyer locks have been acquired. At
        # PostgreSQL READ COMMITTED this statement sees assignments committed by
        # a transaction that held the same Lawyer lock before us.
        active_counts = await self._active_case_counts()
        rows: list[dict] = []
        for lawyer in lawyers:
            email = str(lawyer.email or "").strip().lower()
            if not email or email not in login_emails:
                continue
            rows.append(
                self._capacity_payload(
                    lawyer,
                    active_counts.get(int(lawyer.id), 0),
                )
            )
        return rows

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

        login_emails = await self._active_lawyer_login_emails()
        result = await self.db.execute(
            select(Lawyer, func.coalesce(workload.c.active_cases, 0))
            .outerjoin(workload, workload.c.lawyer_id == Lawyer.id)
            .where(Lawyer.is_active.is_(True))
            .order_by(Lawyer.full_name.asc(), Lawyer.id.asc())
        )

        lawyers: list[dict] = []
        for lawyer, active_cases in result.all():
            lawyer_email = str(lawyer.email or "").strip().lower()
            if not lawyer_email or lawyer_email not in login_emails:
                continue
            lawyers.append(self._capacity_payload(lawyer, int(active_cases)))
        return lawyers

    @staticmethod
    def _assert_expected_snapshot(
        case: Case,
        *,
        expected_lawyer_id=_UNSET,
        expected_status: str | None = None,
    ) -> None:
        if expected_lawyer_id is not _UNSET:
            normalized_lawyer_id = (
                int(expected_lawyer_id)
                if expected_lawyer_id not in (None, "")
                else None
            )
            if case.assigned_lawyer_id != normalized_lawyer_id:
                raise ValueError(
                    "Назначение дела изменилось после загрузки экрана. Обновите данные"
                )
        if expected_status is not None and str(case.status) != str(expected_status):
            raise ValueError(
                "Статус дела изменился после загрузки экрана. Обновите данные"
            )

    async def assign_case(
        self,
        *,
        case_id: int,
        lawyer_id: int,
        actor_type: str,
        actor_id: int | None,
        comment: str | None = None,
        allow_overload: bool = False,
        expected_lawyer_id=_UNSET,
        expected_status: str | None = None,
    ) -> Case:
        case = await self._get_case(case_id, for_update=True)
        self._assert_expected_snapshot(
            case,
            expected_lawyer_id=expected_lawyer_id,
            expected_status=expected_status,
        )
        lawyer = await self._get_lawyer(lawyer_id, for_update=True)
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
        expected_lawyer_id=_UNSET,
        expected_status: str | None = None,
    ) -> Case | None:
        case = await self._get_case(case_id, for_update=True)
        self._assert_expected_snapshot(
            case,
            expected_lawyer_id=expected_lawyer_id,
            expected_status=expected_status,
        )
        if case.assigned_lawyer_id is not None:
            return case
        self._ensure_case_can_be_assigned(case)

        lawyer_rows = await self._locked_active_lawyers()
        best = self._choose_best_lawyer(lawyer_rows)
        if best is None:
            return None

        # The row was locked by _locked_active_lawyers(); fetching it again in
        # this transaction is only to obtain the ORM model used downstream.
        lawyer = await self._get_lawyer(best["id"], for_update=True)
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
                Case.status.in_(AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES),
            )
            .order_by(Case.created_at.asc(), Case.id.asc())
            .limit(safe_limit)
            .with_for_update(skip_locked=True)
        )
        cases = list(result.scalars().all())
        lawyer_rows = await self._locked_active_lawyers()

        assigned: list[dict] = []
        for case in cases:
            best = self._choose_best_lawyer(lawyer_rows)
            if best is None:
                break
            lawyer = await self._get_lawyer(best["id"], for_update=True)
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

        # Exact retry of an already visible assignment must not fail merely
        # because the current case itself fills the last capacity slot.
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

        workload_limit = max(int(lawyer.workload_limit or 0), 0)
        if workload_limit == 0 and not allow_overload:
            raise ValueError("У юриста отсутствует доступная ёмкость")
        if active_cases >= workload_limit and not allow_overload:
            raise ValueError("У юриста достигнут лимит активных дел")

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
        if not automatic_assignment_required(case.status):
            raise ValueError(
                "Назначение юриста доступно только для рабочего M1 после передачи документов. "
                "Ранние этапы остаются за клиентом, а M2 ведёт юрист выбранного консультационного слота."
            )

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

    async def _get_lawyer(
        self,
        lawyer_id: int,
        *,
        for_update: bool = False,
    ) -> Lawyer:
        query = select(Lawyer).where(Lawyer.id == lawyer_id)
        if for_update:
            query = query.with_for_update()
        lawyer = (await self.db.execute(query)).scalar_one_or_none()
        if lawyer is None:
            raise LookupError("Юрист не найден")
        await self._assert_lawyer_login_ready(lawyer)
        return lawyer

    async def _count_active_cases(self, lawyer_id: int) -> int:
        result = await self.db.execute(
            select(func.count(Case.id)).where(
                Case.assigned_lawyer_id == lawyer_id,
                Case.status.notin_(tuple(CLOSED_CASE_STATUSES)),
            )
        )
        return int(result.scalar_one())
