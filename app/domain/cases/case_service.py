from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.statuses.case_statuses import (
    CLOSED_CASE_STATUSES,
    CaseStatus,
    RouteCode,
)
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.user import User


class CaseAssignmentError(RuntimeError):
    """A lawyer cannot be safely assigned to the case."""


def generate_case_number(case_id: int) -> str:
    return f"DLC-{datetime.now().year}-{case_id:06d}"


def _enum_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


class CaseService:
    CLOSED_STATUSES = CLOSED_CASE_STATUSES
    M2_REUSABLE_UNROUTED_STATUSES = frozenset(
        {
            CaseStatus.NEW.value,
            CaseStatus.CALCULATOR_STARTED.value,
            CaseStatus.CALCULATED.value,
            CaseStatus.CLIENT_DECISION.value,
        }
    )

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_active_cases_for_user(
        self,
        user_id: int,
        *,
        route: str | RouteCode | None = None,
    ) -> list[Case]:
        statement = (
            select(Case)
            .where(Case.client_id == user_id)
            .where(Case.status.notin_(self.CLOSED_STATUSES))
        )
        if route is not None:
            statement = statement.where(Case.route == _enum_value(route))
        result = await self.db.execute(
            statement.order_by(Case.created_at.desc(), Case.id.desc())
        )
        return list(result.scalars().all())

    async def get_active_case_for_user(
        self,
        user_id: int,
        *,
        route: str | RouteCode | None = None,
    ):
        cases = await self.list_active_cases_for_user(user_id, route=route)
        return cases[0] if cases else None

    async def get_or_create_m2_case_for_user(self, client: User) -> Case:
        """Return an active M2 case without mutating an existing M1 case.

        An unrouted intake/calculation may safely become M2. A live M1 case is
        preserved and a dedicated consultation case is created instead.
        """

        existing_m2 = await self.get_active_case_for_user(
            client.id,
            route=RouteCode.M2,
        )
        if existing_m2 is not None:
            return existing_m2

        active_cases = await self.list_active_cases_for_user(client.id)
        reusable = next(
            (
                case
                for case in active_cases
                if case.route is None
                and case.status in self.M2_REUSABLE_UNROUTED_STATUSES
            ),
            None,
        )
        if reusable is not None:
            return await self.transfer_to_m2(
                case=reusable,
                actor_type="client",
                actor_id=client.id,
                reason="Клиент начал оформление юридической консультации",
            )

        return await self.create_case(
            client=client,
            route=RouteCode.M2.value,
            status=CaseStatus.M2_DESCRIPTION_PENDING.value,
            title="Юридическая консультация",
        )

    async def create_case(
        self,
        *,
        client: User,
        route: str | None = None,
        status: str = CaseStatus.NEW.value,
        title: str | None = None,
    ):
        normalized_status = _enum_value(status)
        normalized_route = _enum_value(route) if route is not None else None
        case = Case(
            case_number="TEMP",
            client_id=client.id,
            route=normalized_route,
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
                "route": normalized_route,
                "status": case.status,
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
        normalized_status = _enum_value(next_status)
        if case.status == normalized_status:
            return case

        old = {
            "status": case.status,
            "route": case.route,
            "next_action": case.next_action,
        }
        case.status = normalized_status
        if normalized_status.startswith("M1_"):
            case.route = RouteCode.M1.value
        if normalized_status.startswith("M2_"):
            case.route = RouteCode.M2.value
        case.next_action = self.get_next_action(normalized_status)
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
        await self.db.flush()
        return case

    async def assign_lawyer(
        self,
        *,
        case: Case,
        lawyer_id: int,
        actor_id: int | None,
    ):
        if case is None:
            raise CaseAssignmentError("Дело для назначения не найдено.")

        locked_case = (
            await self.db.execute(
                select(Case).where(Case.id == case.id).with_for_update()
            )
        ).scalar_one_or_none()
        if locked_case is None:
            raise CaseAssignmentError("Дело для назначения не найдено.")
        if locked_case.status in self.CLOSED_STATUSES:
            raise CaseAssignmentError("Нельзя назначить юриста на закрытое дело.")

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

        # The same assignment is idempotent and remains valid even when the
        # lawyer is exactly at the configured limit. Every new assignment is
        # checked while the lawyer row is locked, so admin, CRM and automatic
        # flows cannot bypass capacity through a direct CaseService call.
        if locked_case.assigned_lawyer_id != lawyer_id:
            workload_limit = max(int(lawyer.workload_limit or 0), 0)
            current_workload = int(
                (
                    await self.db.execute(
                        select(func.count(Case.id)).where(
                            Case.assigned_lawyer_id == lawyer_id,
                            Case.status.notin_(self.CLOSED_STATUSES),
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

        active_consultations = list(
            (
                await self.db.execute(
                    select(Consultation)
                    .where(Consultation.case_id == locked_case.id)
                    .where(
                        Consultation.status.notin_(
                            ConsultationService.INACTIVE_STATUSES
                        )
                    )
                    .order_by(Consultation.created_at.desc(), Consultation.id.desc())
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        if len(active_consultations) > 1:
            raise CaseAssignmentError(
                "Для дела найдено несколько активных консультаций. "
                "Сначала устраните конфликт данных."
            )
        if active_consultations:
            consultation = active_consultations[0]
            if consultation.lawyer_id is not None and consultation.lawyer_id != lawyer_id:
                raise CaseAssignmentError(
                    "Дело связано с консультацией другого юриста. "
                    "Сначала перенесите консультацию на новый слот."
                )

            if consultation.slot_id is not None:
                slot = (
                    await self.db.execute(
                        select(ConsultationSlot)
                        .where(ConsultationSlot.id == consultation.slot_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if slot is None:
                    raise CaseAssignmentError(
                        "Связанный с консультацией слот не найден. "
                        "Сначала устраните конфликт данных."
                    )
                if slot.consultation_id not in {None, consultation.id}:
                    raise CaseAssignmentError(
                        "Выбранный слот связан с другой консультацией. "
                        "Сначала устраните конфликт данных."
                    )
                if slot.lawyer_id != lawyer_id:
                    raise CaseAssignmentError(
                        "Дело связано со слотом другого юриста. "
                        "Сначала перенесите консультацию на новый слот."
                    )
                if (
                    consultation.lawyer_id is not None
                    and consultation.lawyer_id != slot.lawyer_id
                ):
                    raise CaseAssignmentError(
                        "Юрист консультации не совпадает с юристом выбранного слота. "
                        "Сначала устраните конфликт данных."
                    )

        if locked_case.assigned_lawyer_id == lawyer_id:
            return locked_case

        old = {"assigned_lawyer_id": locked_case.assigned_lawyer_id}
        locked_case.assigned_lawyer_id = lawyer_id
        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=actor_id,
            case_id=locked_case.id,
            action="LAWYER_ASSIGNED",
            old_value=old,
            new_value={"assigned_lawyer_id": lawyer_id},
        )
        await self.db.flush()
        return locked_case

    async def transfer_to_m2(
        self,
        *,
        case: Case,
        actor_type: str,
        actor_id: int | None,
        reason: str,
    ):
        if case.route == RouteCode.M1.value:
            raise ValueError("Действующее дело М1 нельзя преобразовать в консультацию М2.")
        if (
            case.route == RouteCode.M2.value
            and case.status == CaseStatus.M2_DESCRIPTION_PENDING.value
        ):
            return case

        old = {
            "route": case.route,
            "status": case.status,
            "next_action": case.next_action,
        }
        case.route = RouteCode.M2.value
        case.status = CaseStatus.M2_DESCRIPTION_PENDING.value
        case.next_action = self.get_next_action(case.status)
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="CASE_TRANSFERRED_TO_M2",
            old_value=old,
            new_value={
                "route": RouteCode.M2.value,
                "status": case.status,
                "reason": reason,
            },
            comment=reason,
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
        case.route = RouteCode.M1.value
        case.status = CaseStatus.M1_DOCUMENTS_PENDING.value
        case.next_action = self.get_next_action(case.status)
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="CASE_TRANSFERRED_TO_M1",
            new_value={"route": RouteCode.M1.value, "status": case.status},
            comment=comment,
        )
        await self.db.flush()
        return case

    @staticmethod
    def get_next_action(status: str | CaseStatus) -> str:
        value = _enum_value(status)
        actions = {
            CaseStatus.NEW.value: "Начать расчет или связаться с юристом",
            CaseStatus.CALCULATOR_STARTED.value: "Завершить ввод данных для расчета",
            CaseStatus.CALCULATED.value: "Проверить расчет и выбрать дальнейший маршрут",
            CaseStatus.CLIENT_DECISION.value: "Выбрать полное ведение дела или консультацию",
            CaseStatus.M1_DOCUMENTS_PENDING.value: "Загрузить документы по делу",
            CaseStatus.M1_DOCUMENTS_RECEIVED.value: "Ожидать первичной проверки документов",
            CaseStatus.M1_LAWYER_REVIEW.value: "Ожидать решения юриста по делу",
            CaseStatus.M1_DOCS_REQUESTED.value: "Загрузить недостающие документы",
            CaseStatus.M1_ACCEPTED.value: "Ознакомиться с условиями ведения дела",
            CaseStatus.M1_REJECTED.value: "Выбрать консультацию или создать новое обращение",
            CaseStatus.M1_CONTRACT_READY.value: "Ознакомиться с договором и подтвердить решение",
            CaseStatus.M1_WAITING_PAYMENT_30000.value: "Оплатить первый платеж",
            CaseStatus.M1_PAYMENT_30000_RECEIVED.value: "Оформить доверенность",
            CaseStatus.M1_POWER_OF_ATTORNEY.value: "Загрузить оформленную доверенность",
            CaseStatus.M1_POA_RECEIVED.value: "Ожидать проверки доверенности сотрудником",
            CaseStatus.M1_CLAIM_PREPARATION.value: "Ожидать готовности претензии",
            CaseStatus.M1_CLAIM_SENT.value: "Контролировать дату получения претензии",
            CaseStatus.M1_WAITING_30_DAYS.value: "Ожидать окончания установленного срока",
            CaseStatus.M1_COURT_STAGE.value: "Следить за судебным этапом",
            CaseStatus.M1_WAITING_PAYMENT_70000.value: "Оплатить судебный этап",
            CaseStatus.M1_PAYMENT_70000_RECEIVED.value: "Ожидать подготовки документов в суд",
            CaseStatus.M1_ENFORCEMENT.value: "Следить за исполнением решения",
            CaseStatus.M1_MONEY_RECEIVED.value: "Подтвердить получение денежных средств",
            CaseStatus.M1_WAITING_SUCCESS_FEE.value: "Оплатить итоговое вознаграждение",
            CaseStatus.M1_SUCCESS_FEE_RECEIVED.value: "Ожидать закрытия дела",
            CaseStatus.M1_CLOSED.value: "Дело завершено; доступна история и архив",
            CaseStatus.M2_CONSULTATION_ROUTE.value: "Перейти к описанию ситуации",
            CaseStatus.M2_DESCRIPTION_PENDING.value: "Описать ситуацию для юриста",
            CaseStatus.M2_DOCUMENTS_OPTIONAL.value: "Загрузить документы или перейти к выбору времени",
            CaseStatus.M2_SLOT_PENDING.value: "Выбрать время консультации",
            CaseStatus.M2_PAYMENT_PENDING.value: "Оплатить консультацию",
            CaseStatus.M2_CONSULTATION_BOOKED.value: "Ожидать консультации в выбранное время",
            CaseStatus.M2_CONSULTATION_DONE.value: "Ознакомиться с итогом и выбрать следующий шаг",
            CaseStatus.M2_TO_M1.value: "Подтвердить переход к полному ведению дела",
            CaseStatus.M2_CLOSED.value: "Консультация завершена; доступна история",
            CaseStatus.ERROR.value: "Связаться с поддержкой или повторить безопасный шаг",
            CaseStatus.ARCHIVED.value: "Открыть архив дела или создать новое обращение",
        }
        return actions.get(value, "Связаться с поддержкой для определения следующего шага")
