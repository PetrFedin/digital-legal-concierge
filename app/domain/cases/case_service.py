from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_transition_policy import (
    CaseTransitionError,
    TERMINAL_STATUSES,
    normalize_status,
    validate_transition,
)
from app.domain.cases.sla_service import CaseSLAService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.models.case import Case
from app.models.case_creation_request import CaseCreationRequest
from app.models.client_case_context import ClientCaseContext
from app.models.user import User


def generate_case_number(case_id: int) -> str:
    return f"DLC-{datetime.now().year}-{case_id:06d}"


_CLIENT_DOCUMENT_COLLECTION_STATUSES = {
    CaseStatus.M1_DOCUMENTS_PENDING,
    CaseStatus.M1_DOCUMENTS_RECEIVED,
    CaseStatus.M1_DOCS_REQUESTED,
}
_TERMINAL_CASE_VALUES = {
    CaseStatus.M1_CLOSED.value,
    CaseStatus.M2_CLOSED.value,
    CaseStatus.ARCHIVED.value,
}


class CaseSelectionRequired(RuntimeError):
    """Raised when a mutating flow cannot determine which client Case it owns."""


class CaseService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_case_for_user(self, *, user_id: int, case_id: int) -> Case | None:
        return (
            await self.db.execute(
                select(Case).where(
                    Case.id == int(case_id),
                    Case.client_id == int(user_id),
                )
            )
        ).scalar_one_or_none()

    async def get_active_cases_for_user(self, user_id: int) -> list[Case]:
        result = await self.db.execute(
            select(Case)
            .where(Case.client_id == int(user_id))
            .where(Case.status.notin_(_TERMINAL_CASE_VALUES))
            .order_by(Case.created_at.desc(), Case.id.desc())
        )
        return list(result.scalars().all())

    async def get_selected_case_for_user(
        self,
        user_id: int,
        *,
        include_terminal: bool = True,
    ) -> Case | None:
        query = (
            select(Case)
            .join(
                ClientCaseContext,
                ClientCaseContext.selected_case_id == Case.id,
            )
            .where(
                ClientCaseContext.client_id == int(user_id),
                Case.client_id == int(user_id),
            )
        )
        if not include_terminal:
            query = query.where(Case.status.notin_(_TERMINAL_CASE_VALUES))
        return (await self.db.execute(query)).scalar_one_or_none()

    async def _set_selected_case_locked(
        self,
        *,
        client_id: int,
        case_id: int,
    ) -> None:
        context = (
            await self.db.execute(
                select(ClientCaseContext)
                .where(ClientCaseContext.client_id == int(client_id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if context is None:
            context = ClientCaseContext(
                client_id=int(client_id),
                selected_case_id=int(case_id),
            )
            self.db.add(context)
        else:
            context.selected_case_id = int(case_id)
        await self.db.flush()

    async def select_case_for_user(self, *, user_id: int, case_id: int) -> Case:
        """Select an active Case as the current Telegram cabinet context.

        Selection is navigation state only. It never closes, merges or otherwise
        changes another active matter belonging to the same client. The target
        Case is locked and revalidated as active before the persisted client
        context changes, so a stale selector cannot point the cabinet at a Case
        that became terminal after the selector was rendered.
        """

        locked_client = (
            await self.db.execute(
                select(User).where(User.id == int(user_id)).with_for_update()
            )
        ).scalar_one_or_none()
        if locked_client is None:
            raise LookupError("Клиент не найден")

        case = (
            await self.db.execute(
                select(Case)
                .where(
                    Case.id == int(case_id),
                    Case.client_id == int(user_id),
                    Case.status.notin_(_TERMINAL_CASE_VALUES),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if case is None:
            raise LookupError(
                "Обращение уже завершено, недоступно или принадлежит другому клиенту"
            )
        await self._set_selected_case_locked(
            client_id=int(user_id),
            case_id=int(case.id),
        )
        return case

    async def get_active_case_for_user(self, user_id: int) -> Case | None:
        """Return the active Case selected for the Telegram cabinet.

        Legacy callers historically assumed a client-wide singleton. The
        selected-case context makes that assumption explicit without forbidding
        several live matters. If old data has no context yet, the newest active
        Case becomes the deterministic navigation default.
        """

        selected = await self.get_selected_case_for_user(
            int(user_id),
            include_terminal=False,
        )
        if selected is not None:
            return selected
        active_cases = await self.get_active_cases_for_user(int(user_id))
        return active_cases[0] if active_cases else None

    async def create_case_for_operation(
        self,
        *,
        client: User,
        operation_key: str,
        purpose: str,
        route: str | None = None,
        status: str | CaseStatus = CaseStatus.NEW,
        title: str | None = None,
    ) -> Case:
        """Create exactly one Case for one explicit source operation.

        The stable User row is locked only to serialize short creation attempts.
        The lock does *not* enforce one active Case per client: a different
        operation_key creates a different Case. The ledger's unique constraint
        is the database backstop for duplicate Telegram delivery/retries.
        """

        clean_key = str(operation_key or "").strip()
        clean_purpose = str(purpose or "").strip()
        if not clean_key:
            raise ValueError("operation_key обязателен для создания обращения")
        if len(clean_key) > 255:
            raise ValueError("operation_key превышает 255 символов")
        if not clean_purpose:
            raise ValueError("purpose обязателен для создания обращения")
        if len(clean_purpose) > 50:
            raise ValueError("purpose превышает 50 символов")

        locked_client = (
            await self.db.execute(
                select(User)
                .where(User.id == int(client.id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked_client is None:
            raise LookupError("Клиент не найден")

        existing_request = (
            await self.db.execute(
                select(CaseCreationRequest).where(
                    CaseCreationRequest.client_id == int(locked_client.id),
                    CaseCreationRequest.operation_key == clean_key,
                )
            )
        ).scalar_one_or_none()
        if existing_request is not None:
            existing_case = await self.get_case_for_user(
                user_id=int(locked_client.id),
                case_id=int(existing_request.case_id),
            )
            if existing_case is None:
                raise RuntimeError(
                    "Идемпотентная запись создания обращения ссылается на отсутствующее дело"
                )

            # Idempotency answers only which Case this already-processed source
            # operation created. A delayed duplicate is not a fresh navigation
            # command and therefore must never re-select that Case, regardless
            # of whether it is still active or already terminal. The first
            # creation transaction selected it; later explicit navigation is a
            # separate user action.
            return existing_case

        case = await self.create_case(
            client=locked_client,
            route=route,
            status=status,
            title=title,
        )
        self.db.add(
            CaseCreationRequest(
                client_id=int(locked_client.id),
                operation_key=clean_key,
                purpose=clean_purpose,
                case_id=int(case.id),
            )
        )
        await self._set_selected_case_locked(
            client_id=int(locked_client.id),
            case_id=int(case.id),
        )
        await self.db.flush()
        return case

    async def get_or_create_active_case_for_user(
        self,
        client: User,
        *,
        route: str | None = None,
        status: str | CaseStatus = CaseStatus.NEW,
        title: str | None = None,
    ) -> Case:
        """Compatibility helper for flows that operate on the selected Case.

        New entry points that intentionally create a legal matter must use
        ``create_case_for_operation`` with source provenance. This helper never
        imposes a client-wide uniqueness rule; it only reuses the currently
        selected active Case, or bootstraps one when no active matter exists.
        """

        selected = await self.get_active_case_for_user(int(client.id))
        if selected is not None:
            return selected

        # Serialize only the legacy zero-active bootstrap. Two *different* new
        # matter entry points must not use this helper.
        locked_client = (
            await self.db.execute(
                select(User)
                .where(User.id == int(client.id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked_client is None:
            raise LookupError("Клиент не найден")
        selected = await self.get_active_case_for_user(int(locked_client.id))
        if selected is not None:
            return selected
        case = await self.create_case(
            client=locked_client,
            route=route,
            status=status,
            title=title,
        )
        await self._set_selected_case_locked(
            client_id=int(locked_client.id),
            case_id=int(case.id),
        )
        return case

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

    async def _lock_case_for_transition(self, case: Case) -> Case:
        """Serialize every legal status mutation on the persisted Case row.

        Callers may have loaded the Case before another worker committed a legal
        fact. Flush caller-prepared non-status fields, then re-read the row under
        ``FOR UPDATE`` with ``populate_existing`` so transition validation always
        runs against the database truth that actually won the race.
        """

        if getattr(case, "id", None) is None:
            raise CaseTransitionError("Нельзя изменить статус несохранённого дела")
        await self.db.flush()
        locked = (
            await self.db.execute(
                select(Case)
                .where(Case.id == int(case.id))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if locked is None:
            raise LookupError("Дело не найдено")
        return locked

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
        case = await self._lock_case_for_transition(case)
        source, destination = validate_transition(
            case.status,
            next_status,
            force=force,
            actor_type=actor_type,
            comment=comment,
        )
        if source == destination:
            return case, False
        if (
            getattr(case, "content_deleted_at", None) is not None
            and destination not in TERMINAL_STATUSES
        ):
            raise CaseTransitionError(
                "Нельзя повторно открыть дело после удаления его содержимого"
            )

        old = {
            "status": source.value,
            "route": case.route,
            "next_action": case.next_action,
            "closed_at": case.closed_at.isoformat() if case.closed_at else None,
        }
        case.status = destination
        now = datetime.now(timezone.utc)
        if destination in TERMINAL_STATUSES:
            if case.closed_at is None:
                case.closed_at = now
        elif source in TERMINAL_STATUSES and force:
            case.closed_at = None
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
                "closed_at": case.closed_at.isoformat() if case.closed_at else None,
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

    @staticmethod
    def _normalize_client_document_handoff(
        *,
        case: Case,
        next_status: str | CaseStatus,
        actor_type: str,
        comment: str | None,
    ) -> tuple[str | CaseStatus, str | None]:
        """A client may hand documents over, but cannot claim lawyer review began."""
        if str(actor_type or "").strip().lower() != "client":
            return next_status, comment
        source = normalize_status(case.status)
        destination = normalize_status(next_status)
        if (
            destination != CaseStatus.M1_LAWYER_REVIEW
            or source not in _CLIENT_DOCUMENT_COLLECTION_STATUSES
        ):
            return next_status, comment
        clean_comment = str(comment or "").strip()
        suffix = (
            "Документы зарегистрированы у юридической команды; "
            "начало содержательной проверки фиксирует юрист отдельным действием."
        )
        return (
            CaseStatus.M1_DOCUMENTS_RECEIVED,
            f"{clean_comment} {suffix}".strip(),
        )

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
        # Client handoff normalization also depends on the current legal state,
        # so refresh that state under the same row lock before interpreting the
        # requested target. ``_transition`` intentionally re-locks the same row;
        # PostgreSQL treats that as a re-entrant lock within this transaction.
        case = await self._lock_case_for_transition(case)
        next_status, comment = self._normalize_client_document_handoff(
            case=case,
            next_status=next_status,
            actor_type=actor_type,
            comment=comment,
        )
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
            CaseStatus.M1_DOCUMENTS_RECEIVED: "Ожидать назначения и начала проверки",
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


__all__ = [
    "CaseService",
    "CaseSelectionRequired",
    "CaseTransitionError",
    "generate_case_number",
]
