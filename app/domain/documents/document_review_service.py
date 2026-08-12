from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_responsibility import (
    TERMINAL_CASE_STATUS_VALUES,
    effective_lawyer_ids_for_cases,
    lawyer_can_access_case,
)
from app.domain.cases.case_service import CaseService
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.document_statuses import DocumentStatus
from app.lawyer.lawyer_decisions import LawyerDecisionService
from app.models.case import Case
from app.models.document import Document
from app.models.user import User
from app.security.document_access import DocumentActor


class DocumentReviewError(ValueError):
    pass


DECISION_TARGETS = {
    "approve": DocumentStatus.APPROVED,
    "request_reupload": DocumentStatus.NEEDS_REUPLOAD,
    "reject": DocumentStatus.REJECTED,
}

DECISION_EVENTS = {
    "approve": "DOCUMENT_APPROVED",
    "request_reupload": "DOCUMENT_REUPLOAD_REQUESTED",
    "reject": "DOCUMENT_REJECTED",
}

DECISION_LABELS = {
    "approve": "Принят юристом",
    "request_reupload": "Нужно загрузить новую версию",
    "reject": "Документ отклонён",
}


@dataclass(frozen=True)
class ReviewResult:
    document: Document
    case: Case
    changed: bool
    decision: str
    notification_ids: tuple[int, ...] = ()


class DocumentReviewService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def validate_decision(decision: str, comment: str) -> tuple[DocumentStatus, str]:
        normalized = str(decision or "").strip().lower()
        target = DECISION_TARGETS.get(normalized)
        if not target:
            raise DocumentReviewError("Выберите допустимое решение по документу")
        clean_comment = str(comment or "").strip()
        if normalized in {"request_reupload", "reject"} and len(clean_comment) < 10:
            raise DocumentReviewError(
                "Для отклонения или запроса новой версии укажите причину — минимум 10 символов"
            )
        if normalized == "approve" and clean_comment and len(clean_comment) < 3:
            raise DocumentReviewError(
                "Комментарий к принятию должен содержать не менее 3 символов"
            )
        return target, clean_comment

    @staticmethod
    def assert_snapshot(
        document: Document,
        *,
        expected_status: object | None,
        expected_version: object | None,
        expected_updated_at: object | None,
    ) -> None:
        if expected_status is not None and str(document.status) != str(expected_status):
            raise DocumentReviewError(
                "Статус документа изменился после загрузки экрана. Обновите очередь"
            )
        if expected_version is not None:
            try:
                expected_version_int = int(expected_version)
            except (TypeError, ValueError) as error:
                raise DocumentReviewError("Некорректный snapshot версии документа") from error
            if int(document.version or 0) != expected_version_int:
                raise DocumentReviewError(
                    "Версия документа изменилась после загрузки экрана. Обновите очередь"
                )
        actual_updated_at = (
            document.updated_at.isoformat() if document.updated_at else None
        )
        if (
            expected_updated_at is not None
            and actual_updated_at != str(expected_updated_at)
        ):
            raise DocumentReviewError(
                "Документ был изменён после загрузки экрана. Обновите очередь"
            )

    async def ensure_actor_can_review(self, actor: DocumentActor, case: Case) -> None:
        if str(case.status) in TERMINAL_CASE_STATUS_VALUES:
            raise DocumentReviewError(
                "Закрытое дело доступно только для просмотра. Решение по документу уже нельзя изменять"
            )
        if actor.role == "lawyer" and not await lawyer_can_access_case(
            self.db,
            case=case,
            lawyer_id=actor.lawyer_id,
        ):
            raise DocumentReviewError(
                "Документ относится к делу, за которое текущий юрист не отвечает"
            )

    async def _lawyer_review_case_ids(
        self,
        *,
        actor: DocumentActor,
        case_id: int | None,
    ) -> list[int]:
        if actor.role != "lawyer" or actor.lawyer_id is None:
            return []

        statement = (
            select(Case)
            .join(Document, Document.case_id == Case.id)
            .where(Document.status == DocumentStatus.ON_REVIEW)
            .where(Case.status.notin_(TERMINAL_CASE_STATUS_VALUES))
            .distinct()
        )
        if case_id is not None:
            statement = statement.where(Case.id == int(case_id))
        cases = list((await self.db.execute(statement)).scalars().all())
        if not cases:
            return []
        responsibilities = await effective_lawyer_ids_for_cases(self.db, cases)
        return [
            int(case.id)
            for case in cases
            if responsibilities.get(int(case.id)) == int(actor.lawyer_id)
        ]

    async def queue(
        self,
        *,
        actor: DocumentActor,
        case_id: int | None = None,
    ) -> list[dict[str, object]]:
        statement = (
            select(Document, Case, User)
            .join(Case, Case.id == Document.case_id)
            .join(User, User.id == Case.client_id)
            .where(Document.status == DocumentStatus.ON_REVIEW)
            .where(Case.status.notin_(TERMINAL_CASE_STATUS_VALUES))
            .order_by(Document.created_at.asc(), Document.id.asc())
            .limit(300)
        )
        if case_id is not None:
            statement = statement.where(Case.id == int(case_id))
        if actor.role == "lawyer":
            owned_case_ids = await self._lawyer_review_case_ids(
                actor=actor,
                case_id=case_id,
            )
            if not owned_case_ids:
                return []
            statement = statement.where(Case.id.in_(owned_case_ids))
        rows = (await self.db.execute(statement)).all()
        return [
            {
                "document_id": document.id,
                "case_id": case.id,
                "case_number": case.case_number,
                "case_status": case.status,
                "case_updated_at": case.updated_at.isoformat(),
                "client_name": user.full_name,
                "document_type": document.document_type,
                "title": document.title,
                "file_name": document.file_name,
                "mime_type": document.mime_type,
                "file_size": document.file_size,
                "status": document.status,
                "version": document.version,
                "lawyer_comment": document.lawyer_comment,
                "created_at": document.created_at.isoformat(),
                "updated_at": document.updated_at.isoformat(),
            }
            for document, case, user in rows
        ]

    async def _request_new_version(
        self,
        *,
        actor: DocumentActor,
        case: Case,
        document: Document,
        comment: str,
    ) -> None:
        case_status = CaseStatus(str(case.status))
        if case.route != "M1" or case_status not in {
            CaseStatus.M1_DOCUMENTS_RECEIVED,
            CaseStatus.M1_LAWYER_REVIEW,
        }:
            return
        request_comment = f"{document.title}: {comment}"
        if actor.role == "lawyer":
            await LawyerDecisionService(self.db).request_more_documents(
                case=case,
                lawyer_id=actor.lawyer_id or 0,
                comment=request_comment,
            )
            return
        await CaseService(self.db).change_status(
            case=case,
            next_status=CaseStatus.M1_DOCS_REQUESTED,
            actor_type="admin_user",
            actor_id=actor.account_id,
            comment=request_comment,
        )

    async def _archive_previous_versions(
        self,
        *,
        actor: DocumentActor,
        case: Case,
        document: Document,
    ) -> list[int]:
        previous = list(
            (
                await self.db.execute(
                    select(Document)
                    .where(Document.case_id == case.id)
                    .where(Document.document_type == document.document_type)
                    .where(Document.id != document.id)
                    .where(Document.version < document.version)
                    .where(Document.status != DocumentStatus.ARCHIVED)
                    .with_for_update()
                )
            ).scalars().all()
        )
        if not previous:
            return []
        archived = []
        old_values = []
        for item in previous:
            old_values.append(
                {
                    "document_id": item.id,
                    "version": item.version,
                    "status": str(item.status),
                }
            )
            item.status = DocumentStatus.ARCHIVED
            archived.append(item.id)
        await add_case_history_event(
            self.db,
            actor_type="lawyer" if actor.role == "lawyer" else "admin_user",
            actor_id=actor.lawyer_id or actor.account_id,
            case_id=case.id,
            action="DOCUMENT_PREVIOUS_VERSIONS_ARCHIVED",
            old_value={"documents": old_values},
            new_value={
                "current_document_id": document.id,
                "current_version": document.version,
                "archived_document_ids": archived,
            },
            comment="Предыдущие версии архивированы после принятия новой версии",
        )
        return archived

    async def review(
        self,
        *,
        actor: DocumentActor,
        document_id: int,
        decision: str,
        comment: str,
        expected_status: object | None,
        expected_version: object | None,
        expected_updated_at: object | None,
    ) -> ReviewResult:
        target, clean_comment = self.validate_decision(decision, comment)
        normalized = str(decision).strip().lower()
        document = (
            await self.db.execute(
                select(Document)
                .where(Document.id == int(document_id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not document:
            raise DocumentReviewError("Документ не найден")
        case = (
            await self.db.execute(
                select(Case)
                .where(Case.id == document.case_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not case:
            raise DocumentReviewError("Дело документа не найдено")
        await self.ensure_actor_can_review(actor, case)

        current_status = DocumentStatus(str(document.status))
        if current_status == target:
            if str(document.lawyer_comment or "").strip() == clean_comment:
                return ReviewResult(document, case, False, normalized, ())
            raise DocumentReviewError(
                "Решение уже принято с другим комментарием. Обновите карточку"
            )

        self.assert_snapshot(
            document,
            expected_status=expected_status,
            expected_version=expected_version,
            expected_updated_at=expected_updated_at,
        )
        if current_status != DocumentStatus.ON_REVIEW:
            raise DocumentReviewError(
                "Документ уже вышел из очереди проверки. Обновите список"
            )

        old_value = {
            "document_id": document.id,
            "status": str(document.status),
            "version": document.version,
            "lawyer_comment": document.lawyer_comment,
        }
        document.status = target
        document.lawyer_comment = clean_comment or None

        if normalized in {"request_reupload", "reject"}:
            await self._request_new_version(
                actor=actor,
                case=case,
                document=document,
                comment=clean_comment,
            )
        elif normalized == "approve":
            await self._archive_previous_versions(
                actor=actor,
                case=case,
                document=document,
            )

        await add_case_history_event(
            self.db,
            actor_type="lawyer" if actor.role == "lawyer" else "admin_user",
            actor_id=actor.lawyer_id or actor.account_id,
            case_id=case.id,
            action="DOCUMENT_REVIEW_DECISION",
            old_value=old_value,
            new_value={
                "document_id": document.id,
                "status": str(target),
                "version": document.version,
                "decision": normalized,
            },
            comment=clean_comment or DECISION_LABELS[normalized],
        )
        notifications = await NotificationEngine(self.db).emit(
            event_code=DECISION_EVENTS[normalized],
            case_id=case.id,
            user_id=case.client_id,
            payload={
                "case_number": case.case_number,
                "document": document.title,
                "status": DECISION_LABELS[normalized],
                "comment": clean_comment or "Без замечаний",
            },
            dedupe_key=(
                f"case:{case.id}:document:{document.id}:"
                f"review:{document.version}:{normalized}"
            ),
        )
        await self.db.flush()
        notification_ids = tuple(
            int(notification.id)
            for notification in notifications
            if notification.id is not None
        )
        return ReviewResult(
            document,
            case,
            True,
            normalized,
            notification_ids,
        )
