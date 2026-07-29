from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.sla_service import CaseSLAService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.models.case import Case
from app.models.user import User


def generate_case_number(case_id: int) -> str:
    return f"DLC-{datetime.now().year}-{case_id:06d}"


class CaseService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_active_case_for_user(self, user_id: int):
        result = await self.db.execute(
            select(Case)
            .where(Case.client_id == user_id)
            .where(
                Case.status.notin_(
                    [
                        CaseStatus.M1_CLOSED,
                        CaseStatus.M2_CLOSED,
                        CaseStatus.ARCHIVED,
                    ]
                )
            )
            .order_by(Case.created_at.desc())
        )
        return result.scalars().first()

    async def create_case(
        self,
        *,
        client: User,
        route: str | None = None,
        status: str = CaseStatus.NEW,
        title: str | None = None,
    ):
        case = Case(
            case_number="TEMP",
            client_id=client.id,
            route=route,
            status=status,
            title=title or "Обращение по ДДУ",
            next_action=self.get_next_action(status),
        )
        self.db.add(case)
        await self.db.flush()
        case.case_number = generate_case_number(case.id)
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
            },
        )
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
    ):
        old = {
            "status": case.status,
            "route": case.route,
            "next_action": case.next_action,
        }
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
            new_value={
                "status": case.status,
                "route": case.route,
                "next_action": case.next_action,
            },
            comment=comment,
        )
        await CaseSLAService(self.db).synchronize_case_status(
            case=case,
            actor_type=actor_type,
            actor_id=actor_id,
            comment=(
                f"Синхронизация SLA после статуса {next_status}. "
                f"{comment or ''}"
            ).strip(),
        )
        await self.db.flush()
        return case

    async def assign_lawyer(
        self,
        *,
        case: Case,
        lawyer_id: int,
        actor_id: int,
    ):
        old = {"assigned_lawyer_id": case.assigned_lawyer_id}
        await CaseSLAService(self.db).start_assignment_sla(
            case=case,
            lawyer_id=lawyer_id,
            actor_type="admin",
            actor_id=actor_id,
            comment="Назначение юриста через CaseService",
        )
        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=actor_id,
            case_id=case.id,
            action="LAWYER_ASSIGNED",
            old_value=old,
            new_value={"assigned_lawyer_id": lawyer_id},
        )
        await self.db.flush()
        return case

    async def transfer_to_m2(
        self,
        *,
        case: Case,
        actor_type: str,
        actor_id: int | None,
        reason: str,
    ):
        case.route = RouteCode.M2
        case.status = CaseStatus.M2_DESCRIPTION_PENDING
        case.next_action = self.get_next_action(case.status)
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="CASE_TRANSFERRED_TO_M2",
            new_value={
                "route": "M2",
                "status": case.status,
                "reason": reason,
            },
            comment=reason,
        )
        await CaseSLAService(self.db).synchronize_case_status(
            case=case,
            actor_type=actor_type,
            actor_id=actor_id,
            comment="Синхронизация SLA после перевода в М2",
        )
        await self.db.flush()
        return case

    async def transfer_to_m1(
        self,
        *,
        case: Case,
        actor_type: str,
        actor_id: int | None,
        comment: str | None = None,
    ):
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
        await CaseSLAService(self.db).synchronize_case_status(
            case=case,
            actor_type=actor_type,
            actor_id=actor_id,
            comment="Синхронизация SLA после перевода в М1",
        )
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
            CaseStatus.M2_DOCUMENTS_OPTIONAL: (
                "Загрузить документы при наличии"
            ),
            CaseStatus.M2_SLOT_PENDING: "Выбрать время консультации",
            CaseStatus.M2_PAYMENT_PENDING: "Оплатить консультацию",
            CaseStatus.M2_CONSULTATION_BOOKED: "Ожидать консультации",
            CaseStatus.M2_CONSULTATION_DONE: "Ожидать решения юриста",
            CaseStatus.M2_CLOSED: "Обращение закрыто",
        }
        return mapping.get(status, "Ожидать следующего действия")
