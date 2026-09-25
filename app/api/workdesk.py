from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import require_admin
from app.api.case_action_ui import CASE_ACTION_HTML
from app.api.workdesk_ui import WORKDESK_HTML
from app.config import settings
from app.db.session import get_db
from app.domain.cases.assignment_policy import automatic_assignment_required
from app.domain.cases.case_timeline import get_client_visible_status
from app.domain.cases.service_modes import M1ServiceMode
from app.domain.documents.document_workflow import (
    DocumentAttentionState,
    DocumentWorkflowDescriptor,
    describe_document_attention,
)
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.document_statuses import DocumentStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.document import Document
from app.models.lawyer import Lawyer
from app.models.message import Message
from app.models.payment import Payment
from app.models.self_filing_package import SelfFilingPackage
from app.models.user import User

router = APIRouter(tags=["admin-workdesk"])

CLOSED_STATUSES = ("M1_CLOSED", "M1_SELF_FILING_CLOSED", "M2_CLOSED", "ARCHIVED")
OVERDUE_SLA_STATUSES = ("FIRST_RESPONSE_OVERDUE", "ACTION_OVERDUE")
ACTIVE_CONSULTATION_STATUSES = ("BOOKED", "CONFIRMED")
CASE_ACTION_TASKS = {"documents", "consultation", "sla"}
FINANCIAL_ATTENTION_STATUSES = (
    PaymentStatus.PAID_REVIEW,
    PaymentStatus.REFUND_PENDING,
)

ROUTE_LABELS = {
    "M1": "Ведение дела",
    "M2": "Консультация",
}
SELF_FILING_STAFF_STATUSES = {
    "M1_SELF_FILING_DOCUMENTS_RECEIVED",
    "M1_SELF_FILING_LAWYER_REVIEW",
    "M1_SELF_FILING_PREPARATION",
    "M1_SELF_FILING_READY",
}

SLA_LABELS = {
    "NOT_STARTED": "SLA не запущен",
    "FIRST_RESPONSE_PENDING": "Ожидается первая реакция",
    "FIRST_RESPONSE_OK": "Первая реакция в срок",
    "FIRST_RESPONSE_OVERDUE": "Просрочена первая реакция",
    "ACTION_PENDING": "Ожидается действие",
    "ACTION_OK": "Действие выполнено в срок",
    "ACTION_OVERDUE": "Действие просрочено",
}

_REASON_PRIORITY = {
    "payment_review": 0,
    "refund": 1,
    "overdue": 2,
    "messages": 3,
    "unassigned": 4,
    "self_filing": 5,
    "documents": 5,
    "document_draft": 6,
    "document_legacy": 6,
    "consultation": 7,
}
_REASON_LABELS = {
    "payment_review": "Полученный платёж требует сверки",
    "refund": "Возврат ждёт обработки",
    "overdue": "Нарушен SLA",
    "messages": "Новое сообщение клиента",
    "unassigned": "Нет ответственного юриста",
    "self_filing": "Пакет самостоятельной подачи требует действия",
    "documents": "Документы ждут решения",
    "document_draft": "Файлы ещё не переданы юристу",
    "document_legacy": "Статус документов требует уточнения",
    "consultation": "Консультация сегодня",
}


def _route_label(route: str | None) -> str:
    return ROUTE_LABELS.get(str(route or ""), "Юридическое обращение")


def _sla_label(status: str | None) -> str:
    return SLA_LABELS.get(str(status or ""), "SLA не определён")


def _utc_sort_value(value: datetime | None) -> float:
    if value is None:
        return float("inf")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).timestamp()


def _empty_payment_attention() -> dict[str, object]:
    return {
        "review_ids": [],
        "refund_ids": [],
        "review_oldest_at": None,
        "refund_oldest_at": None,
    }


def _append_payment_attention(
    attention: dict[str, object],
    *,
    payment_id: int,
    status: object,
    activity_at: datetime | None,
) -> None:
    status_value = str(status)
    if status_value == str(PaymentStatus.PAID_REVIEW):
        ids = attention["review_ids"]
        assert isinstance(ids, list)
        ids.append(int(payment_id))
        current = attention.get("review_oldest_at")
        if current is None or _utc_sort_value(activity_at) < _utc_sort_value(current):
            attention["review_oldest_at"] = activity_at
    elif status_value == str(PaymentStatus.REFUND_PENDING):
        ids = attention["refund_ids"]
        assert isinstance(ids, list)
        ids.append(int(payment_id))
        current = attention.get("refund_oldest_at")
        if current is None or _utc_sort_value(activity_at) < _utc_sort_value(current):
            attention["refund_oldest_at"] = activity_at


def _attention_reasons(
    case: Case,
    *,
    unread_client_messages: int,
    document_workflow: DocumentWorkflowDescriptor,
    consultation_at: datetime | None,
    payment_attention: dict[str, object],
) -> list[dict[str, object]]:
    codes: list[str] = []
    review_ids = payment_attention.get("review_ids") or []
    refund_ids = payment_attention.get("refund_ids") or []
    if review_ids:
        codes.append("payment_review")
    if refund_ids:
        codes.append("refund")
    if str(case.sla_status or "") in OVERDUE_SLA_STATUSES:
        codes.append("overdue")
    if unread_client_messages > 0:
        codes.append("messages")
    if case.assigned_lawyer_id is None and automatic_assignment_required(case.status):
        codes.append("unassigned")
    if str(case.status) in SELF_FILING_STAFF_STATUSES:
        codes.append("self_filing")
    if document_workflow.state != DocumentAttentionState.NONE:
        codes.append(document_workflow.code)
    if consultation_at is not None:
        codes.append("consultation")

    reasons: list[dict[str, object]] = []
    for code in sorted(codes, key=_REASON_PRIORITY.__getitem__):
        label = _REASON_LABELS[code]
        if code == "payment_review":
            label = f"Платежи требуют сверки: {len(review_ids)}"
        elif code == "refund":
            label = f"Возвраты ждут обработки: {len(refund_ids)}"
        elif code == "messages":
            label = f"Новые сообщения клиента: {unread_client_messages}"
        elif code == document_workflow.code:
            label = document_workflow.label
        reasons.append(
            {
                "code": code,
                "label": label,
                "priority": _REASON_PRIORITY[code],
            }
        )
    return reasons


def _primary_action(
    case: Case,
    reasons: list[dict[str, object]],
    document_workflow: DocumentWorkflowDescriptor,
    payment_attention: dict[str, object],
) -> dict[str, object]:
    reason_codes = {str(item["code"]) for item in reasons}
    review_ids = payment_attention.get("review_ids") or []
    refund_ids = payment_attention.get("refund_ids") or []
    if "payment_review" in reason_codes and review_ids:
        payment_id = int(review_ids[0])
        if (
            str(getattr(case, "service_mode", "") or "")
            == M1ServiceMode.SELF_FILING_PACKAGE.value
        ):
            return {
                "kind": "link",
                "label": "Сверить оплату пакета 15 000 ₽",
                "href": f"/self-filing/ui?case_id={case.id}",
            }
        return {
            "kind": "link",
            "label": "Сверить полученный платёж",
            "href": f"/admin/payment-reviews/ui?payment_id={payment_id}&case_id={case.id}",
        }
    if "refund" in reason_codes and refund_ids:
        payment_id = int(refund_ids[0])
        return {
            "kind": "link",
            "label": "Обработать возврат",
            "href": f"/admin/refunds/ui?payment_id={payment_id}&case_id={case.id}",
        }
    # An overdue case without a lawyer cannot be acknowledged on the SLA screen:
    # the case-scoped SLA projection intentionally requires an assignee. Keep the
    # card ranked by the overdue reason, but make the visible CTA satisfy the
    # prerequisite first so the operator never lands on an empty action screen.
    if "overdue" in reason_codes and "unassigned" in reason_codes:
        return {
            "kind": "auto_assign",
            "label": "Назначить юриста для устранения SLA",
            "endpoint": f"/admin/cases/{case.id}/auto-assign",
            "payload": {
                "expected_lawyer_id": None,
                "expected_status": str(case.status),
            },
        }
    # The visible primary action must follow the same ordering as the queue.
    # Otherwise a lower-priority assignment button can hide an SLA breach or a
    # client message even though the card itself is sorted above by that reason.
    if "overdue" in reason_codes:
        return {
            "kind": "link",
            "label": "Устранить просрочку",
            "href": f"/admin/workdesk/cases/{case.id}/action/sla",
        }
    if "messages" in reason_codes:
        return {
            "kind": "link",
            "label": "Прочитать сообщение клиента",
            "href": f"/message-center/ui?case_id={case.id}",
        }
    if "unassigned" in reason_codes:
        return {
            "kind": "auto_assign",
            "label": "Назначить юриста",
            "endpoint": f"/admin/cases/{case.id}/auto-assign",
            "payload": {
                "expected_lawyer_id": None,
                "expected_status": str(case.status),
            },
        }
    if "self_filing" in reason_codes:
        return {
            "kind": "link",
            "label": "Открыть пакет самостоятельной подачи",
            "href": f"/self-filing/ui?case_id={case.id}",
        }
    if "documents" in reason_codes:
        return {
            "kind": "link",
            "label": document_workflow.primary_label,
            "href": f"/admin/workdesk/cases/{case.id}/action/documents",
        }
    if {"document_draft", "document_legacy"} & reason_codes:
        return {
            "kind": "link",
            "label": document_workflow.primary_label,
            "href": f"/message-center/ui?case_id={case.id}",
        }
    return {
        "kind": "link",
        "label": "Открыть консультацию дела",
        "href": f"/admin/workdesk/cases/{case.id}/action/consultation",
    }


def _attention_item(
    case: Case,
    *,
    unread_client_messages: int,
    latest_client_message_at: datetime | None,
    document_workflow: DocumentWorkflowDescriptor,
    consultation_at: datetime | None,
    lawyer_name: str | None,
    payment_attention: dict[str, object],
    self_filing_due_at: datetime | None = None,
) -> dict[str, object] | None:
    reasons = _attention_reasons(
        case,
        unread_client_messages=unread_client_messages,
        document_workflow=document_workflow,
        consultation_at=consultation_at,
        payment_attention=payment_attention,
    )
    if not reasons:
        return None

    primary_reason = str(reasons[0]["code"])
    if primary_reason == "payment_review":
        deadline = payment_attention.get("review_oldest_at")
    elif primary_reason == "refund":
        deadline = payment_attention.get("refund_oldest_at")
    elif primary_reason == "overdue":
        deadline = case.sla_due_at
    elif primary_reason == "messages":
        deadline = latest_client_message_at
    elif primary_reason == "self_filing" and self_filing_due_at is not None:
        deadline = self_filing_due_at
    elif consultation_at is not None:
        deadline = consultation_at
    else:
        deadline = case.created_at

    review_ids = [int(value) for value in payment_attention.get("review_ids") or []]
    refund_ids = [int(value) for value in payment_attention.get("refund_ids") or []]
    return {
        "id": case.id,
        "number": case.case_number,
        "route": case.route,
        "route_label": (
            "Пакет для самостоятельной подачи"
            if str(getattr(case, "service_mode", "") or "")
            == M1ServiceMode.SELF_FILING_PACKAGE.value
            else _route_label(case.route)
        ),
        "status": str(case.status),
        "status_label": get_client_visible_status(case.status),
        "lawyer_id": case.assigned_lawyer_id,
        "lawyer_name": lawyer_name,
        "next_action": case.next_action
        or "Проверить карточку и определить следующий этап",
        "sla_status": case.sla_status,
        "sla_label": _sla_label(case.sla_status),
        "sla_due_at": case.sla_due_at.isoformat() if case.sla_due_at else None,
        "consultation_at": consultation_at.isoformat() if consultation_at else None,
        "self_filing_sla_due_at": (
            self_filing_due_at.isoformat() if self_filing_due_at else None
        ),
        "unread_client_messages": unread_client_messages,
        "latest_client_message_at": (
            latest_client_message_at.isoformat()
            if latest_client_message_at
            else None
        ),
        "document_workflow": document_workflow.as_dict(),
        "financial_attention": {
            "payment_review_ids": review_ids,
            "refund_pending_ids": refund_ids,
        },
        "created_at": case.created_at.isoformat(),
        "updated_at": case.updated_at.isoformat(),
        "priority": int(reasons[0]["priority"]),
        "reasons": reasons,
        "primary_action": _primary_action(
            case,
            reasons,
            document_workflow,
            payment_attention,
        ),
        "sort_deadline": _utc_sort_value(deadline),
    }


def _attention_sort_key(item: dict[str, object]) -> tuple[object, ...]:
    return (
        int(item["priority"]),
        float(item["sort_deadline"]),
        str(item["created_at"]),
        int(item["id"]),
    )


def _render_case_action_html(case_id: int) -> str:
    """Bind the generic action screen to server-scoped read projections.

    Mutating endpoints remain owned by their domain modules. Only list/read calls
    are replaced, so a deep link cannot silently fall outside a paginated global
    queue when the installation grows.
    """

    html = CASE_ACTION_HTML
    replacements = (
        (
            "api('/document-access/review/queue')",
            f"api('/admin/workdesk/cases/{case_id}/documents')",
        ),
        (
            "api('/admin/consultation-outcomes'),",
            f"api('/admin/workdesk/cases/{case_id}/consultation-outcomes'),",
        ),
        (
            "api('/admin/work-queues/consultations')",
            f"api('/admin/workdesk/cases/{case_id}/consultations-today')",
        ),
        (
            "api('/admin/sla?overdue_only=false')",
            f"api('/admin/workdesk/cases/{case_id}/sla')",
        ),
    )
    for source, target in replacements:
        if html.count(source) != 1:
            raise RuntimeError(
                f"Case action template contract changed for {source!r}"
            )
        html = html.replace(source, target, 1)
    return html


@router.get("/admin/workdesk/attention")
async def workdesk_attention(
    limit: int = 12,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    bounded_limit = min(max(int(limit), 1), 50)
    now = datetime.now(timezone.utc)
    today_start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    tomorrow_start = today_start + timedelta(days=1)

    cases = list(
        (
            await db.execute(
                select(Case)
                .where(Case.status.notin_(CLOSED_STATUSES))
                .order_by(Case.created_at.asc(), Case.id.asc())
            )
        ).scalars().all()
    )
    if not cases:
        return {
            "count": 0,
            "total": 0,
            "truncated": False,
            "items": [],
            "generated_at": now.isoformat(),
        }

    case_ids = [case.id for case in cases]
    document_rows = (
        await db.execute(
            select(Document.case_id, Document.status)
            .where(Document.case_id.in_(case_ids))
        )
    ).all()
    document_statuses: dict[int, list[object]] = {}
    for case_id, status in document_rows:
        document_statuses.setdefault(int(case_id), []).append(status)
    document_workflows = {
        case_id: describe_document_attention(statuses)
        for case_id, statuses in document_statuses.items()
    }
    no_document_workflow = describe_document_attention(())

    payment_rows = (
        await db.execute(
            select(
                Payment.case_id,
                Payment.id,
                Payment.status,
                Payment.updated_at,
                Payment.created_at,
            )
            .where(Payment.case_id.in_(case_ids))
            .where(Payment.status.in_(FINANCIAL_ATTENTION_STATUSES))
            .order_by(Payment.created_at.asc(), Payment.id.asc())
        )
    ).all()
    financial_attention: dict[int, dict[str, object]] = {}
    for case_id, payment_id, payment_status, updated_at, created_at in payment_rows:
        item = financial_attention.setdefault(int(case_id), _empty_payment_attention())
        _append_payment_attention(
            item,
            payment_id=int(payment_id),
            status=payment_status,
            activity_at=updated_at or created_at,
        )

    self_filing_rows = (
        await db.execute(
            select(
                SelfFilingPackage.case_id,
                SelfFilingPackage.sla_due_at,
            ).where(SelfFilingPackage.case_id.in_(case_ids))
        )
    ).all()
    self_filing_due: dict[int, datetime | None] = {
        int(case_id): due_at for case_id, due_at in self_filing_rows
    }

    message_rows = (
        await db.execute(
            select(
                Message.case_id,
                func.count(Message.id),
                func.max(Message.created_at),
            )
            .where(Message.case_id.in_(case_ids))
            .where(Message.sender_type == "client")
            .where(Message.is_read.is_(False))
            .group_by(Message.case_id)
        )
    ).all()
    unread_messages: dict[int, tuple[int, datetime | None]] = {
        int(case_id): (int(count or 0), latest_at)
        for case_id, count, latest_at in message_rows
    }

    consultation_rows = (
        await db.execute(
            select(Consultation.case_id, Consultation.scheduled_at)
            .where(Consultation.case_id.in_(case_ids))
            .where(Consultation.status.in_(ACTIVE_CONSULTATION_STATUSES))
            .where(Consultation.scheduled_at >= today_start)
            .where(Consultation.scheduled_at < tomorrow_start)
            .order_by(Consultation.scheduled_at.asc())
        )
    ).all()
    consultation_times: dict[int, datetime] = {}
    for case_id, scheduled_at in consultation_rows:
        if scheduled_at is None:
            continue
        existing = consultation_times.get(int(case_id))
        if existing is None or (
            _utc_sort_value(scheduled_at) < _utc_sort_value(existing)
        ):
            consultation_times[int(case_id)] = scheduled_at

    lawyer_ids = {
        int(case.assigned_lawyer_id)
        for case in cases
        if case.assigned_lawyer_id is not None
    }
    lawyer_names: dict[int, str] = {}
    if lawyer_ids:
        lawyers = list(
            (
                await db.execute(select(Lawyer).where(Lawyer.id.in_(lawyer_ids)))
            ).scalars().all()
        )
        lawyer_names = {int(item.id): item.full_name for item in lawyers}

    items: list[dict[str, object]] = []
    for case in cases:
        unread_count, latest_message_at = unread_messages.get(case.id, (0, None))
        item = _attention_item(
            case,
            unread_client_messages=unread_count,
            latest_client_message_at=latest_message_at,
            document_workflow=document_workflows.get(
                case.id,
                no_document_workflow,
            ),
            consultation_at=consultation_times.get(case.id),
            lawyer_name=lawyer_names.get(case.assigned_lawyer_id),
            payment_attention=financial_attention.get(
                case.id,
                _empty_payment_attention(),
            ),
            self_filing_due_at=self_filing_due.get(int(case.id)),
        )
        if item is not None:
            items.append(item)
    items.sort(key=_attention_sort_key)
    total = len(items)
    visible = items[:bounded_limit]
    for item in visible:
        item.pop("sort_deadline", None)
    return {
        "count": len(visible),
        "total": total,
        "truncated": total > len(visible),
        "items": visible,
        "generated_at": now.isoformat(),
    }


@router.get("/admin/workdesk/cases/{case_id}/documents")
async def workdesk_case_documents(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    case_row = (
        await db.execute(
            select(Case, User)
            .join(User, User.id == Case.client_id)
            .where(Case.id == case_id)
        )
    ).one_or_none()
    if case_row is None:
        raise HTTPException(status_code=404, detail="Дело не найдено")
    case, user = case_row
    documents = list(
        (
            await db.execute(
                select(Document)
                .where(Document.case_id == case_id)
                .order_by(Document.created_at.asc(), Document.id.asc())
            )
        ).scalars().all()
    )
    workflow = describe_document_attention(document.status for document in documents)
    actionable_documents = [
        document
        for document in documents
        if document.status == DocumentStatus.ON_REVIEW
    ]
    items = [
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
            "review_started_at": (
                document.review_started_at.isoformat()
                if document.review_started_at
                else None
            ),
            "created_at": document.created_at.isoformat(),
            "updated_at": document.updated_at.isoformat(),
        }
        for document in actionable_documents
    ]
    return {
        "role": "admin",
        "case_id": case_id,
        "workflow": workflow.as_dict(),
        "count": len(items),
        "items": items,
    }


@router.get("/admin/workdesk/cases/{case_id}/consultation-outcomes")
async def workdesk_case_consultation_outcomes(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    statement = (
        select(Consultation, Case, User, ConsultationSlot, Lawyer)
        .join(Case, Case.id == Consultation.case_id)
        .join(User, User.id == Case.client_id)
        .join(ConsultationSlot, ConsultationSlot.id == Consultation.slot_id)
        .join(Lawyer, Lawyer.id == Consultation.lawyer_id)
        .where(Case.id == case_id)
        .where(
            or_(
                and_(
                    Consultation.status == ConsultationStatus.BOOKED,
                    ConsultationSlot.starts_at <= cutoff,
                ),
                Consultation.status == ConsultationStatus.LAWYER_NO_SHOW,
            )
        )
        .order_by(ConsultationSlot.starts_at.asc())
    )
    rows = (await db.execute(statement)).all()
    return [
        {
            "consultation_id": consultation.id,
            "case_id": case.id,
            "case_number": case.case_number,
            "client_name": user.full_name,
            "client_telegram_id": user.telegram_id,
            "lawyer_id": lawyer.id,
            "lawyer_name": lawyer.full_name,
            "status": consultation.status,
            "slot_id": slot.id,
            "slot_status": slot.status,
            "starts_at": slot.starts_at.isoformat(),
            "ends_at": slot.ends_at.isoformat(),
            "next_action": case.next_action,
        }
        for consultation, case, user, slot, lawyer in rows
    ]


@router.get("/admin/workdesk/cases/{case_id}/consultations-today")
async def workdesk_case_consultations_today(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    now = datetime.now(timezone.utc)
    today_start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    tomorrow_start = today_start + timedelta(days=1)
    consultation_id = (
        await db.execute(
            select(Consultation.id)
            .where(Consultation.case_id == case_id)
            .where(Consultation.status.in_(ACTIVE_CONSULTATION_STATUSES))
            .where(Consultation.scheduled_at >= today_start)
            .where(Consultation.scheduled_at < tomorrow_start)
            .order_by(Consultation.scheduled_at.asc(), Consultation.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return {
        "count": 1 if consultation_id is not None else 0,
        "items": ([{"id": case_id}] if consultation_id is not None else []),
        "generated_at": now.isoformat(),
    }


@router.get("/admin/workdesk/cases/{case_id}/sla")
async def workdesk_case_sla(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    row = (
        await db.execute(
            select(Case, Lawyer, User)
            .join(Lawyer, Lawyer.id == Case.assigned_lawyer_id)
            .join(User, User.id == Case.client_id)
            .where(Case.id == case_id)
            .where(Case.assigned_lawyer_id.is_not(None))
            .where(Case.sla_status != "NOT_STARTED")
        )
    ).one_or_none()
    if row is None:
        return []
    case, lawyer, user = row
    return [
        {
            "case_id": case.id,
            "case_number": case.case_number,
            "route": case.route,
            "case_status": case.status,
            "next_action": case.next_action,
            "client_id": user.id,
            "client_name": user.full_name,
            "client_telegram_id": user.telegram_id,
            "lawyer_id": lawyer.id,
            "lawyer_name": lawyer.full_name,
            "sla_status": case.sla_status,
            "sla_due_at": (
                case.sla_due_at.isoformat() if case.sla_due_at else None
            ),
            "assigned_at": (
                case.assigned_at.isoformat() if case.assigned_at else None
            ),
            "first_lawyer_response_at": (
                case.first_lawyer_response_at.isoformat()
                if case.first_lawyer_response_at
                else None
            ),
            "last_lawyer_activity_at": (
                case.last_lawyer_activity_at.isoformat()
                if case.last_lawyer_activity_at
                else None
            ),
            "escalation_level": int(case.escalation_level or 0),
        }
    ]


@router.get("/admin/workdesk/ui", response_class=HTMLResponse)
async def workdesk_ui(request: Request):
    token = request.headers.get("x-admin-token") or request.cookies.get(
        settings.admin_session_cookie
    )
    if not token:
        return RedirectResponse(url="/login", status_code=303)
    require_admin(token)
    return HTMLResponse(WORKDESK_HTML)


@router.get(
    "/admin/workdesk/cases/{case_id}/action/{task}",
    response_class=HTMLResponse,
)
async def workdesk_case_action(
    case_id: int,
    task: str,
    request: Request,
):
    if task not in CASE_ACTION_TASKS:
        raise HTTPException(status_code=404, detail="Неизвестное действие по делу")
    token = request.headers.get("x-admin-token") or request.cookies.get(
        settings.admin_session_cookie
    )
    if not token:
        return RedirectResponse(url="/login", status_code=303)
    require_admin(token)
    return HTMLResponse(_render_case_action_html(case_id))
