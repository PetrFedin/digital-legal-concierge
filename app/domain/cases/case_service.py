from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.workflow_engine import WorkflowEngine
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.models.case import Case
from app.models.user import User


CLOSED_STATUSES = {CaseStatus.M1_CLOSED, CaseStatus.M2_CLOSED, CaseStatus.ARCHIVED}


def generate_case_number(case_id: int) -> str:
    return f"DLC-{datetime.now().year}-{case_id:06d}"


class CaseService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.workflow = WorkflowEngine(db)

    async def list_cases_for_user(self, user_id: int, *, include_archived: bool = False) -> list[Case]:
        query = select(Case).where(Case.client_id == user_id)
        if not include_archived:
            query = query.where(Case.is_archived.is_(False))
        query = query.order_by(Case.is_archived.asc(), Case.created_at.desc())
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_case_for_user(self, *, user_id: int, case_id: int) -> Case | None:
        result = await self.db.execute(
            select(Case).where(Case.id == case_id, Case.client_id == user_id)
        )
        return result.scalars().first()

    async def get_selected_case_for_user(self, user: User) -> Case | None:
        if user.selected_case_id:
            selected = await self.get_case_for_user(user_id=user.id, case_id=user.selected_case_id)
            if selected and not selected.is_archived:
                return selected

        result = await self.db.execute(
            select(Case)
            .where(Case.client_id == user.id, Case.is_archived.is_(False))
            .where(Case.status.notin_(list(CLOSED_STATUSES)))
            .order_by(Case.created_at.desc())
        )
        fallback = result.scalars().first()
        if fallback:
            user.selected_case_id = fallback.id
            await self.db.flush()
        return fallback

    async def get_active_case_for_user(self, user_id: int) -> Case | None:
        result = await self.db.execute(select(User).where(User.id == user_id))
        user = result.scalars().first()
        if not user:
            return None
        return await self.get_selected_case_for_user(user)

    async def select_case(self, *, user: User, case_id: int) -> Case:
        case = await self.get_case_for_user(user_id=user.id, case_id=case_id)
        if not case:
            raise ValueError("Дело не найдено или не принадлежит пользователю")
        if case.is_archived:
            raise ValueError("Архивное дело сначала нужно восстановить")
        user.selected_case_id = case.id
        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=user.id,
            case_id=case.id,
            action="CASE_SELECTED",
            new_value={"selected_case_id": case.id},
        )
        await self.db.flush()
        return case

    async def create_case(
        self,
        *,
        client: User,
        route: str | None = None,
        status: str = CaseStatus.NEW,
        title: str | None = None,
        select_created: bool = True,
    ) -> Case:
        case = Case(
            case_number="TEMP",
            client_id=client.id,
            route=route,
            status=status,
            title=title or "Юридическое обращение",
            next_action=self.get_next_action(status),
            is_archived=False,
        )
        self.db.add(case)
        await self.db.flush()
        case.case_number = generate_case_number(case.id)
        if select_created:
            client.selected_case_id = case.id
        await add_case_history_event(
            self.db,
            actor_type="system",
            actor_id=None,
            case_id=case.id,
            action="CASE_CREATED",
            new_value={
                "case_number": case.case_number,
                "route": route,
                "status": status,
                "selected": select_created,
            },
        )
        await self.workflow.apply_case_status(case, str(status))
        await self.db.flush()
        return case

    async def archive_case(self, *, user: User, case: Case, actor_type: str = "client") -> Case:
        if case.client_id != user.id:
            raise ValueError("Нельзя архивировать чужое дело")
        if case.is_archived:
            return case
        case.is_archived = True
        if user.selected_case_id == case.id:
            user.selected_case_id = None
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=user.id,
            case_id=case.id,
            action="CASE_ARCHIVED",
            old_value={"is_archived": False},
            new_value={"is_archived": True},
        )
        await self.db.flush()
        await self.get_selected_case_for_user(user)
        return case

    async def restore_case(self, *, user: User, case: Case, actor_type: str = "client") -> Case:
        if case.client_id != user.id:
            raise ValueError("Нельзя восстановить чужое дело")
        case.is_archived = False
        user.selected_case_id = case.id
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=user.id,
            case_id=case.id,
            action="CASE_RESTORED",
            old_value={"is_archived": True},
            new_value={"is_archived": False, "selected_case_id": case.id},
        )
        await self.workflow.apply_case_status(case, str(case.status))
        await self.db.flush()
        return case

    async def change_status(
        self,
        *,
        case: Case,
        next_status: str,
        actor_type: str,
        actor_id: int | None = None,
        comment: str | None = None,
        force: bool = True,
    ) -> Case:
        old = {"status": case.status, "route": case.route, "next_action": case.next_action}
        case.status = next_status
        if str(next_status).startswith("M1_"):
            case.route = RouteCode.M1
        if str(next_status).startswith("M2_"):
            case.route = RouteCode.M2
        case.next_action = self.get_next_action(next_status)
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="CASE_STATUS_CHANGED",
            old_value=old,
            new_value={"status": case.status, "route": case.route, "next_action": case.next_action},
            comment=comment,
        )
        await self.workflow.apply_case_status(case, str(next_status))
        await self.db.flush()
        return case

    async def assign_lawyer(self, *, case: Case, lawyer_id: int, actor_id: int):
        old = {"assigned_lawyer_id": case.assigned_lawyer_id}
        case.assigned_lawyer_id = lawyer_id
        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=actor_id,
            case_id=case.id,
            action="LAWYER_ASSIGNED",
            old_value=old,
            new_value={"assigned_lawyer_id": lawyer_id},
        )
        assigned_tasks = await self.workflow.assign_unassigned_tasks(case)
        if assigned_tasks:
            await add_case_history_event(
                self.db,
                actor_type="system",
                actor_id=None,
                case_id=case.id,
                action="WORKFLOW_TASKS_ASSIGNED",
                new_value={"assigned_lawyer_id": lawyer_id, "tasks_count": assigned_tasks},
            )
        await self.db.flush()
        return case

    async def transfer_to_m2(self, *, case: Case, actor_type: str, actor_id: int | None, reason: str):
        case.route = RouteCode.M2
        case.status = CaseStatus.M2_DESCRIPTION_PENDING
        case.next_action = self.get_next_action(case.status)
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="CASE_TRANSFERRED_TO_M2",
            new_value={"route": "M2", "status": case.status, "reason": reason},
            comment=reason,
        )
        await self.workflow.apply_case_status(case, str(case.status))
        await self.db.flush()
        return case

    async def transfer_to_m1(self, *, case: Case, actor_type: str, actor_id: int | None, comment: str | None = None):
        case.route = RouteCode.M1
        case.status = CaseStatus.M1_DOCUMENTS_PENDING
        case.next_action = self.get_next_action(case.status)
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="CASE_TRANSFERRED_TO_M1",
            new_value={"route": "M1", "status": case.status},
            comment=comment,
        )
        await self.workflow.apply_case_status(case, str(case.status))
        await self.db.flush()
        return case

    @staticmethod
    def get_next_action(status: str) -> str:
        mapping = {
            CaseStatus.NEW: "Начать расчет или связаться с юристом",
            CaseStatus.CALCULATED: "Выбрать дальнейший маршрут",
            CaseStatus.M1_DOCUMENTS_PENDING: "Загрузить документы",
            CaseStatus.M1_LAWYER_REVIEW: "Ожидать проверки юристом",
            CaseStatus.M1_CONTRACT_READY: "Подписать договор",
            CaseStatus.M1_WAITING_PAYMENT_30000: "Оплатить первый платеж",
            CaseStatus.M1_POWER_OF_ATTORNEY: "Оформить доверенность",
            CaseStatus.M1_CLAIM_SENT: "Ожидать 30 дней после претензии",
            CaseStatus.M1_COURT_STAGE: "Следить за судебным этапом",
            CaseStatus.M1_WAITING_PAYMENT_70000: "Оплатить второй платеж",
            CaseStatus.M1_ENFORCEMENT: "Ожидать исполнения решения",
            CaseStatus.M1_WAITING_SUCCESS_FEE: "Оплатить финальный процент",
            CaseStatus.M1_CLOSED: "Дело завершено",
            CaseStatus.M2_DESCRIPTION_PENDING: "Описать ситуацию",
            CaseStatus.M2_DOCUMENTS_OPTIONAL: "Загрузить документы при наличии",
            CaseStatus.M2_SLOT_PENDING: "Выбрать время консультации",
            CaseStatus.M2_PAYMENT_PENDING: "Оплатить консультацию",
            CaseStatus.M2_CONSULTATION_BOOKED: "Ожидать консультации",
            CaseStatus.M2_CONSULTATION_DONE: "Ожидать решения юриста",
            CaseStatus.M2_CLOSED: "Обращение закрыто",
        }
        return mapping.get(status, "Ожидать следующего действия")
