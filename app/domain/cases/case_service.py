from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_transition_policy import (
    CaseTransitionError,
    TERMINAL_STATUSES,
    normalize_status,
    validate_initial_status,
    validate_transition,
)
from app.domain.cases.sla_service import CaseSLAService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.models.case import Case
from app.models.case_creation_request import CaseCreationRequest
from app.models.case_transition import CaseTransitionCommand, CaseTransitionOutboxEvent
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
    CaseStatus.M1_SELF_FILING_CLOSED.value,
    CaseStatus.M2_CLOSED.value,
    CaseStatus.ARCHIVED.value,
}


class CaseSelectionRequired(RuntimeError):
    """Raised when a mutating flow cannot determine which client Case it owns."""


@dataclass(frozen=True)
class CaseTransitionResult:
    """Authoritative result of one Case process transition command."""

    case: Case
    outcome: str
    changed: bool
    command_id: int | None
    applied_version: int
    source_status: str
    target_status: str
    result_payload: dict


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
        """Return only an unambiguous active Telegram cabinet Case.

        A valid selected active Case is authoritative. For legacy data without a
        selection, the only active Case is safe to reuse. If two or more active
        matters exist, returning the newest one would let an old unbound callback
        read or mutate the wrong legal matter, so this method deliberately fails
        closed with ``None`` until the client selects a Case explicitly.
        """

        selected = await self.get_selected_case_for_user(
            int(user_id),
            include_terminal=False,
        )
        if selected is not None:
            return selected
        active_cases = await self.get_active_cases_for_user(int(user_id))
        return active_cases[0] if len(active_cases) == 1 else None

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
        ``create_case_for_operation`` with source provenance. This helper only
        reuses an unambiguous active Case or bootstraps when there are zero
        active matters. It must never create a new Case merely because multiple
        active matters require an explicit selection.
        """

        selected = await self.get_active_case_for_user(int(client.id))
        if selected is not None:
            return selected
        if len(await self.get_active_cases_for_user(int(client.id))) > 1:
            raise CaseSelectionRequired(
                "Выберите активное обращение перед продолжением"
            )

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
        if len(await self.get_active_cases_for_user(int(locked_client.id))) > 1:
            raise CaseSelectionRequired(
                "Выберите активное обращение перед продолжением"
            )
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
        status: str | CaseStatus = CaseStatus.NEW,
        title: str | None = None,
    ):
        normalized_status = validate_initial_status(status)
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

    async def _lock_case_for_transition(
        self,
        case: Case,
        *,
        request_client_id: int | None = None,
    ) -> Case:
        """Serialize and re-read the exact persisted Case before a mutation.

        A client-originated command is additionally scoped by client_id.
        Ownership is proven before idempotency recovery or stale-state details
        are evaluated, so a foreign caller cannot probe another client's journal.
        """

        if getattr(case, "id", None) is None:
            raise CaseTransitionError("Нельзя изменить статус несохранённого дела")
        await self.db.flush()
        statement = select(Case).where(Case.id == int(case.id))
        if request_client_id is not None:
            statement = statement.where(
                Case.client_id == int(request_client_id)
            )
        locked = (
            await self.db.execute(
                statement.with_for_update().execution_options(
                    populate_existing=True
                )
            )
        ).scalar_one_or_none()
        if locked is None:
            raise LookupError("Дело не найдено или недоступно")
        return locked

    @staticmethod
    def _transition_payload(case: Case) -> dict:
        return {
            "case_id": int(case.id),
            "status": normalize_status(case.status).value,
            "route": str(case.route) if case.route is not None else None,
            "version": int(case.version or 1),
            "next_action": case.next_action,
            "closed_at": case.closed_at.isoformat() if case.closed_at else None,
        }

    async def _existing_transition_command(
        self,
        *,
        case_id: int,
        idempotency_key: str,
    ) -> CaseTransitionCommand | None:
        return (
            await self.db.execute(
                select(CaseTransitionCommand).where(
                    CaseTransitionCommand.case_id == int(case_id),
                    CaseTransitionCommand.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    def _assert_replay_identity(
        command: CaseTransitionCommand,
        *,
        actor_type: str,
        actor_id: int | None,
        request_client_id: int | None,
        action: str,
        target_status: CaseStatus,
    ) -> None:
        if (
            str(command.actor_type) != actor_type
            or command.actor_id != actor_id
            or command.request_client_id != request_client_id
            or str(command.action) != action
            or str(command.target_status) != target_status.value
        ):
            raise CaseTransitionError(
                "Идемпотентный ключ уже использован другим переходом дела"
            )

    async def execute_transition(
        self,
        *,
        case: Case,
        next_status: str | CaseStatus,
        actor_type: str,
        actor_id: int | None = None,
        comment: str | None = None,
        force: bool = False,
        action: str = "CASE_STATUS_CHANGED",
        expected_version: int | None = None,
        idempotency_key: str | None = None,
        request_client_id: int | None = None,
        correlation_id: str | None = None,
    ) -> CaseTransitionResult:
        """Apply one authoritative M1/M2 process transition.

        Ordering is ownership lock, idempotent replay, optimistic version check,
        policy validation, mutation, journal, audit and outbox. A committed
        command is therefore recoverable before a stale expected version can
        reject its retry, while an unrelated stale action has no side effects.
        """

        normalized_actor = str(actor_type or "").strip().lower()
        if not normalized_actor:
            raise CaseTransitionError("Не указан тип участника перехода")
        normalized_actor_id = int(actor_id) if actor_id is not None else None

        effective_client_id = (
            int(request_client_id) if request_client_id is not None else None
        )
        if normalized_actor == "client":
            if normalized_actor_id is None:
                raise CaseTransitionError(
                    "Клиентский переход требует идентификатор клиента"
                )
            if (
                effective_client_id is not None
                and effective_client_id != normalized_actor_id
            ):
                raise CaseTransitionError(
                    "Клиент не соответствует владельцу команды"
                )
            effective_client_id = normalized_actor_id

        destination = normalize_status(next_status)
        clean_action = str(action or "").strip()
        if not clean_action:
            raise CaseTransitionError("Не указан тип перехода")
        if len(clean_action) > 100:
            raise CaseTransitionError("Тип перехода превышает 100 символов")

        explicit_idempotency = idempotency_key is not None
        clean_key = str(idempotency_key or "").strip()
        if explicit_idempotency and not clean_key:
            raise CaseTransitionError("Пустой idempotency key недопустим")
        if not clean_key:
            clean_key = f"internal:{uuid4()}"
        if len(clean_key) > 255:
            raise CaseTransitionError("Idempotency key превышает 255 символов")

        clean_correlation = str(correlation_id or "").strip() or None
        if clean_correlation and len(clean_correlation) > 255:
            raise CaseTransitionError("Correlation id превышает 255 символов")

        normalized_expected: int | None = None
        if expected_version is not None:
            try:
                normalized_expected = int(expected_version)
            except (TypeError, ValueError) as error:
                raise CaseTransitionError(
                    "Некорректная ожидаемая версия дела"
                ) from error
            if normalized_expected < 1:
                raise CaseTransitionError(
                    "Ожидаемая версия дела должна быть положительной"
                )

        case = await self._lock_case_for_transition(
            case,
            request_client_id=effective_client_id,
        )

        if explicit_idempotency:
            existing = await self._existing_transition_command(
                case_id=int(case.id),
                idempotency_key=clean_key,
            )
            if existing is not None:
                self._assert_replay_identity(
                    existing,
                    actor_type=normalized_actor,
                    actor_id=normalized_actor_id,
                    request_client_id=effective_client_id,
                    action=clean_action,
                    target_status=destination,
                )
                payload = dict(existing.result_payload or {})
                return CaseTransitionResult(
                    case=case,
                    outcome="REPLAYED",
                    changed=False,
                    command_id=int(existing.id),
                    applied_version=int(existing.applied_version),
                    source_status=str(existing.source_status),
                    target_status=str(existing.target_status),
                    result_payload=payload,
                )

        current_version = int(case.version or 1)
        if (
            normalized_expected is not None
            and normalized_expected != current_version
        ):
            payload = self._transition_payload(case)
            return CaseTransitionResult(
                case=case,
                outcome="STALE",
                changed=False,
                command_id=None,
                applied_version=current_version,
                source_status=normalize_status(case.status).value,
                target_status=destination.value,
                result_payload=payload,
            )

        source, destination = validate_transition(
            case.status,
            destination,
            force=force,
            actor_type=normalized_actor,
            comment=comment,
        )

        if (
            source.value.startswith("M2_")
            and destination.value.startswith("M1_")
        ):
            if (
                normalized_actor != "lawyer"
                or normalized_actor_id is None
                or clean_action != "CASE_TRANSFERRED_TO_M1"
            ):
                raise CaseTransitionError(
                    "Перевод M2 в M1 доступен только как решение юриста "
                    "через authority-переход CASE_TRANSFERRED_TO_M1"
                )

        if source == destination:
            payload = self._transition_payload(case)
            return CaseTransitionResult(
                case=case,
                outcome="NOOP",
                changed=False,
                command_id=None,
                applied_version=current_version,
                source_status=source.value,
                target_status=destination.value,
                result_payload=payload,
            )

        if (
            getattr(case, "content_deleted_at", None) is not None
            and destination not in TERMINAL_STATUSES
        ):
            raise CaseTransitionError(
                "Нельзя повторно открыть дело после удаления его содержимого"
            )

        old = {
            "status": source.value,
            "route": str(case.route) if case.route is not None else None,
            "version": current_version,
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
        case.version = current_version + 1

        payload = self._transition_payload(case)
        command = CaseTransitionCommand(
            case_id=int(case.id),
            request_client_id=effective_client_id,
            actor_type=normalized_actor,
            actor_id=normalized_actor_id,
            action=clean_action,
            idempotency_key=clean_key,
            correlation_id=clean_correlation,
            expected_version=normalized_expected,
            applied_version=int(case.version),
            source_status=source.value,
            target_status=destination.value,
            outcome="APPLIED",
            result_payload=payload,
        )
        self.db.add(command)
        await self.db.flush()

        await add_case_history_event(
            self.db,
            actor_type=normalized_actor,
            actor_id=normalized_actor_id,
            case_id=case.id,
            action=clean_action,
            old_value=old,
            new_value={
                **payload,
                "forced": force,
                "transition_command_id": int(command.id),
                "idempotency_key": clean_key,
                "correlation_id": clean_correlation,
            },
            comment=comment,
        )

        outbox = CaseTransitionOutboxEvent(
            event_id=str(uuid4()),
            case_id=int(case.id),
            command_id=int(command.id),
            aggregate_version=int(case.version),
            event_type="CASE_TRANSITION_APPLIED",
            payload={
                "case_id": int(case.id),
                "action": clean_action,
                "source_status": source.value,
                "target_status": destination.value,
                "route": payload["route"],
                "version": int(case.version),
                "actor_type": normalized_actor,
                "actor_id": normalized_actor_id,
                "correlation_id": clean_correlation,
            },
            status="PENDING",
            attempt_count=0,
        )
        self.db.add(outbox)

        await CaseSLAService(self.db).synchronize_case_status(
            case=case,
            actor_type=normalized_actor,
            actor_id=normalized_actor_id,
            comment=(
                f"Синхронизация SLA после статуса {destination.value}. "
                f"{comment or ''}"
            ).strip(),
        )
        await self.db.flush()
        return CaseTransitionResult(
            case=case,
            outcome="APPLIED",
            changed=True,
            command_id=int(command.id),
            applied_version=int(case.version),
            source_status=source.value,
            target_status=destination.value,
            result_payload=payload,
        )

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
        expected_version: int | None = None,
        idempotency_key: str | None = None,
        request_client_id: int | None = None,
        correlation_id: str | None = None,
    ) -> tuple[Case, bool]:
        result = await self.execute_transition(
            case=case,
            next_status=next_status,
            actor_type=actor_type,
            actor_id=actor_id,
            comment=comment,
            force=force,
            action=action,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            request_client_id=request_client_id,
            correlation_id=correlation_id,
        )
        if result.outcome == "STALE":
            raise CaseTransitionError(
                "Состояние дела изменилось после загрузки действия. "
                f"Текущая версия: {result.applied_version}; обновите экран."
            )
        return result.case, result.changed

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
        expected_version: int | None = None,
        idempotency_key: str | None = None,
        request_client_id: int | None = None,
        correlation_id: str | None = None,
    ):
        # Client handoff normalization also depends on the current legal state,
        # so refresh that state under the same row lock before interpreting the
        # requested target. ``_transition`` intentionally re-locks the same row;
        # PostgreSQL treats that as a re-entrant lock within this transaction.
        effective_client_id = request_client_id
        if (
            effective_client_id is None
            and str(actor_type or "").strip().lower() == "client"
            and actor_id is not None
        ):
            effective_client_id = int(actor_id)
        case = await self._lock_case_for_transition(
            case,
            request_client_id=effective_client_id,
        )
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
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            request_client_id=effective_client_id,
            correlation_id=correlation_id,
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

    async def start_self_filing(
        self,
        *,
        case: Case,
        actor_type: str,
        actor_id: int | None,
        comment: str | None = None,
    ):
        case, _changed = await self._transition(
            case=case,
            next_status=CaseStatus.M1_SELF_FILING_PROFILE_PENDING,
            actor_type=actor_type,
            actor_id=actor_id,
            comment=comment,
            force=False,
            action="CASE_SELF_FILING_STARTED",
            request_client_id=(actor_id if actor_type == "client" else None),
        )
        return case

    async def transfer_to_m2(
        self,
        *,
        case: Case,
        actor_type: str,
        actor_id: int | None,
        reason: str,
        expected_version: int | None = None,
        idempotency_key: str | None = None,
        request_client_id: int | None = None,
        correlation_id: str | None = None,
    ):
        case, _changed = await self._transition(
            case=case,
            next_status=CaseStatus.M2_DESCRIPTION_PENDING,
            actor_type=actor_type,
            actor_id=actor_id,
            comment=reason,
            force=False,
            action="CASE_TRANSFERRED_TO_M2",
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            request_client_id=request_client_id,
            correlation_id=correlation_id,
        )
        return case

    async def transfer_to_m1(
        self,
        *,
        case: Case,
        actor_type: str,
        actor_id: int | None,
        comment: str | None = None,
        expected_version: int | None = None,
        idempotency_key: str | None = None,
        request_client_id: int | None = None,
        correlation_id: str | None = None,
    ):
        case, _changed = await self._transition(
            case=case,
            next_status=CaseStatus.M1_DOCUMENTS_PENDING,
            actor_type=actor_type,
            actor_id=actor_id,
            comment=comment,
            force=False,
            action="CASE_TRANSFERRED_TO_M1",
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            request_client_id=request_client_id,
            correlation_id=correlation_id,
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
            CaseStatus.M1_SELF_FILING_PROFILE_PENDING: "Указать регион, адрес и email для выдачи пакета",
            CaseStatus.M1_SELF_FILING_DOCUMENTS_PENDING: "Загрузить ДДУ, паспорт и приложения",
            CaseStatus.M1_SELF_FILING_DOCUMENTS_RECEIVED: "Передать комплект юристу на проверку",
            CaseStatus.M1_SELF_FILING_LAWYER_REVIEW: "Ожидать проверки комплекта и подсудности",
            CaseStatus.M1_SELF_FILING_DOCS_REQUESTED: "Добавить запрошенные документы",
            CaseStatus.M1_SELF_FILING_PAYMENT_PENDING: "Оплатить подготовку пакета 15 000 ₽",
            CaseStatus.M1_SELF_FILING_PREPARATION: "Ожидать подготовку пакета документов",
            CaseStatus.M1_SELF_FILING_READY: "Получить готовый пакет",
            CaseStatus.M1_SELF_FILING_DELIVERED: "Проверить полученный пакет",
            CaseStatus.M1_SELF_FILING_CLOSED: "Услуга подготовки пакета завершена",
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
    "CaseTransitionResult",
    "generate_case_number",
]
