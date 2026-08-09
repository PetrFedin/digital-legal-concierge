from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_timeline import get_client_visible_status
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.document_statuses import DocumentStatus
from app.lawyer.lawyer_decisions import LawyerDecisionService
from app.models.case import Case
from app.models.document import Document
from app.models.user import User
from app.security.document_access import DocumentActor

_REPLACEMENT_STATUSES = {
    DocumentStatus.REJECTED,
    DocumentStatus.NEEDS_REUPLOAD,
}


def _case_status(value: object) -> CaseStatus | None:
    try:
        return CaseStatus(str(value))
    except (TypeError, ValueError):
        return None


def _document_status(value: object) -> DocumentStatus | None:
    try:
        return DocumentStatus(str(value))
    except (TypeError, ValueError):
        return None


def _latest_by_type(documents: list[Document]) -> dict[str, Document]:
    latest: dict[str, Document] = {}
    for document in sorted(
        documents,
        key=lambda item: (
            item.document_type,
            -int(item.version or 0),
            -int(item.id or 0),
        ),
    ):
        latest.setdefault(document.document_type, document)
    return latest


def _readiness_reason(error: ValueError) -> str:
    text = str(error).strip()
    prefix = "Нельзя принять дело: "
    return text[len(prefix) :] if text.startswith(prefix) else text


async def build_document_review_case_context(
    db: AsyncSession,
    *,
    actor: DocumentActor,
    case_id: int,
) -> dict[str, object] | None:
    """Return a role-scoped, domain-derived next action for one document case.

    The document review UI must not infer whether an M1 case is ready for
    acceptance from counters alone. Readiness is delegated to the same
    LawyerDecisionService invariant that protects the actual acceptance write.
    """

    statement = (
        select(Case, User)
        .join(User, User.id == Case.client_id)
        .where(Case.id == int(case_id))
    )
    if actor.role == "lawyer":
        statement = statement.where(Case.assigned_lawyer_id == actor.lawyer_id)
    row = (await db.execute(statement)).first()
    if row is None:
        return None

    case, user = row
    documents = list(
        (
            await db.execute(
                select(Document)
                .where(Document.case_id == case.id)
                .where(Document.status != DocumentStatus.ARCHIVED)
                .order_by(
                    Document.document_type.asc(),
                    Document.version.desc(),
                    Document.id.desc(),
                )
            )
        ).scalars().all()
    )
    latest = list(_latest_by_type(documents).values())

    on_review = sum(
        _document_status(document.status) == DocumentStatus.ON_REVIEW
        for document in latest
    )
    approved = sum(
        _document_status(document.status) == DocumentStatus.APPROVED
        for document in latest
    )
    replacement = sum(
        _document_status(document.status) in _REPLACEMENT_STATUSES
        for document in latest
    )
    uploaded = sum(
        _document_status(document.status) == DocumentStatus.UPLOADED
        for document in latest
    )
    required = sum(
        _document_status(document.status) == DocumentStatus.REQUIRED
        for document in latest
    )
    unknown = sum(_document_status(document.status) is None for document in latest)

    case_status = _case_status(case.status)
    documents_ready = False
    readiness_reason: str | None = None
    if case.route == "M1":
        try:
            await LawyerDecisionService(db).assert_documents_ready_for_acceptance(
                case=case
            )
        except ValueError as error:
            readiness_reason = _readiness_reason(error)
        else:
            documents_ready = True

    can_accept = bool(
        actor.role == "lawyer"
        and case.route == "M1"
        and case_status == CaseStatus.M1_LAWYER_REVIEW
        and documents_ready
    )

    if on_review:
        primary_action = "review_document"
        primary_label = "Проверить документ"
        primary_note = f"Ожидают решения: {on_review}"
    elif replacement:
        primary_action = "wait_client_reupload"
        primary_label = "Ожидать новую версию от клиента"
        primary_note = "Клиент уже получил замечание и действие на замену файла."
    elif uploaded:
        primary_action = "wait_client_submit"
        primary_label = "Ожидать передачу файла клиентом"
        primary_note = (
            "Новая версия уже загружена, но ещё не передана в юридическую очередь проверки."
        )
    elif case_status == CaseStatus.M1_DOCS_REQUESTED:
        primary_action = "wait_client_reupload"
        primary_label = "Ожидать новую версию от клиента"
        primary_note = "Запрос документов уже отправлен клиенту."
    elif can_accept:
        primary_action = "accept_m1_case"
        primary_label = "Принять дело и открыть договор"
        primary_note = "Все актуальные документы приняты юристом."
    elif case.route == "M1" and case_status == CaseStatus.M1_LAWYER_REVIEW:
        primary_action = "open_case"
        primary_label = "Проверить комплект в деле"
        primary_note = readiness_reason or "Комплект пока не готов к принятию."
    else:
        primary_action = "open_case"
        primary_label = "Открыть текущее дело"
        primary_note = case.next_action or "Проверьте актуальный следующий шаг дела."

    return {
        "case_id": case.id,
        "case_number": case.case_number,
        "client_name": user.full_name,
        "route": case.route,
        "case_status": case.status,
        "case_status_label": get_client_visible_status(case.status),
        "case_updated_at": case.updated_at.isoformat(),
        "case_next_action": case.next_action,
        "documents_total": len(latest),
        "documents_on_review": on_review,
        "documents_approved": approved,
        "documents_replacement": replacement,
        "documents_uploaded": uploaded,
        "documents_required": required,
        "documents_unknown": unknown,
        "documents_ready": documents_ready,
        "readiness_reason": readiness_reason,
        "can_accept": can_accept,
        "primary_action": primary_action,
        "primary_label": primary_label,
        "primary_note": primary_note,
        "workspace_url": "/lawyer/workspace/ui",
        "message_url": f"/message-center/ui?case_id={case.id}",
        "review_url": f"/document-access/review/ui?case_id={case.id}",
    }
