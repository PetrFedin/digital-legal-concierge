from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_transition_policy import (
    CaseTransitionError,
    normalize_status,
    validate_transition,
)
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
        normalized_status = normalize_status(status)
        case = Case(
            case_number="TEMP",
            client_id=client.id,
            route=route,
            status=normalized_status,
            title=title or "Обращение по ДДУ",
            next_action=self.get_next_action(normalized_status),
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
                "status": normalized_status.value,
            },
        )
        await self.db.flush()
        return case

    async def _transition(
        self,
        *,
        case: Case,
        next_status: str | CaseStatus,
        actor_type: str,
        actor_id: int | None,
        comment: str | None,
        force: bool,
        action: str,
    ) -> tuple[Case, bool]:
        source, destination = validate_transition(
            case.status,
            next_status,
            force=force,
            actor_type=actor_type,
            comment=comment,
        )
        if source == destination:
            return case, False

        old = {
            "status": source.value,
            "route": case.route,
            "next_action": case.next_action,
        }
        case.status = destination
        if destination.value.startswith("M1_"):
            case.route = RouteCode.M1
        elif destination.value.startswith("M2_"):
            case.route = RouteCode.M2
        case.next_action = self.get_next_action(destination)

        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action=action,
            old_value=old,
            new_value={
                "status": destination.value,
                "route": case.route,
                "next_action": case.next_action,
                "forced": force,
            },
            comment=comment,
        )
        await CaseSLAService(self.db).synchronize_case_status(
            case=case,
            actor_type=actor_type,
            actor_id=actor_id,
            comment=(
                f"Синхронизация SLA после статуса {destination.value}. "
                f"{comment or ''}"
            ).strip(),
        )
        await self.db.flush()
        return case, True

    async def change_status(
        self,
        *,
        case: Case,
        next_status: str | CaseStatus,
        actor_type: str,
        actor_id: int | None = None,
        comment: str | None = None,
        force: bool = False,
    ):
        case, _changed = await self._transition(
            case=case,
            next_status=next_status,
            actor_type=actor_type,
            actor_id=actor_id,
            comment=comment,
            force=force,
            action="CASE_STATUS_CHANGED",
        )
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
        case, _changed = await self._transition(
            case=case,
            next_status=CaseStatus.M2_DESCRIPTION_PENDING,
            actor_type=actor_type,
            actor_id=actor_id,
            comment=reason,
            force=False,
            action="CASE_TRANSFERRED_TO_M2",
        )
        return case

    async def transfer_to_m1(
        self,
        *,
        case: Case,
        actor_type: str,
        actor_id: int | None,
        comment: str | None = None,
    ):
        case, _changed = await self._transition(
            case=case,
            next_status=CaseStatus.M1_DOCUMENTS_PENDING,
            actor_type=actor_type,
            actor_id=actor_id,
            comment=comment,
            force=False,
            action="CASE_TRANSFERRED_TO_M1",
        )
        return case

    @staticmethod
    def get_next_action(status: str | CaseStatus) -> str:
        normalized = normalize_status(status)
        mapping = {
            CaseStatus.NEW: "Начать расчет или связаться с юристом",
            CaseStatus.CALCULATOR_STARTED: "Завершить расчет",
            CaseStatus.CALCULATED: "Выбрать дальнейший маршрут",
            CaseStatus.CLIENT_DECISION: "Выбрать дальнейший маршрут",
            CaseStatus.M1_DOCUMENTS_PENDING: "Загрузить документы",
            CaseStatus.M1_DOCUMENTS_RECEIVED: "Передать документы на проверку",
            CaseStatus.M1_LAWYER_REVIEW: "Ожидать проверки юристом",
            CaseStatus.M1_DOCS_REQUESTED: "Загрузить запрошенные документы",
            CaseStatus.M1_ACCEPTED: "Ожидать договор",
            CaseStatus.M1_REJECTED: "Выбрать консультацию или закрытие",
            CaseStatus.M1_CONTRACT_READY: "Подписать договор",
            CaseStatus.M1_WAITING_PAYMENT_30000: "Оплатить первый платеж",
            CaseStatus.M1_PAYMENT_30000_RECEIVED: "Перейти к доверенности",
            CaseStatus.M1_POWER_OF_ATTORNEY: "Оформить доверенность",
            CaseStatus.M1_POA_RECEIVED: "Ожидать подготовки претензии",
            CaseStatus.M1_CLAIM_PREPARATION: "Ожидать отправки претензии",
            CaseStatus.M1_CLAIM_SENT: "Ожидать начала контрольного срока",
            CaseStatus.M1_WAITING_30_DAYS: "Ожидать 30 дней после претензии",
            CaseStatus.M1_COURT_STAGE: "Следить за судебным этапом",
            CaseStatus.M1_WAITING_PAYMENT_70000: "Оплатить второй платеж",
            CaseStatus.M1_PAYMENT_70000_RECEIVED: "Ожидать исполнения решения",
            CaseStatus.M1_ENFORCEMENT: "Ожидать исполнения решения",
            CaseStatus.M1_MONEY_RECEIVED: "Рассчитать финальный процент",
            CaseStatus.M1_WAITING_SUCCESS_FEE: "Оплатить финальный процент",
            CaseStatus.M1_SUCCESS_FEE_RECEIVED: "Закрыть дело",
            CaseStatus.M1_CLOSED: "Дело завершено",
            CaseStatus.M2_CONSULTATION_ROUTE: "Описать ситуацию",
            CaseStatus.M2_DESCRIPTION_PENDING: "Описать ситуацию",
            CaseStatus.M2_DOCUMENTS_OPTIONAL: "Загрузить документы при наличии",
            CaseStatus.M2_SLOT_PENDING: "Выбрать время консультации",
            CaseStatus.M2_PAYMENT_PENDING: "Оплатить консультацию",
            CaseStatus.M2_CONSULTATION_BOOKED: "Ожидать консультации",
            CaseStatus.M2_CONSULTATION_DONE: "Ожидать решения юриста",
            CaseStatus.M2_TO_M1: "Перейти к документам маршрута М1",
            CaseStatus.M2_CLOSED: "Обращение закрыто",
            CaseStatus.ERROR: "Требуется ручная проверка",
            CaseStatus.ARCHIVED: "Дело находится в архиве",
        }
        return mapping[normalized]


__all__ = ["CaseService", "CaseTransitionError", "generate_case_number"]
