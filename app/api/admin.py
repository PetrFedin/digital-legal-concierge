from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.admin_dashboard import AdminDashboardService
from app.db.session import get_db
from app.domain.assignment.assignment_engine import AssignmentEngine
from app.domain.cases.case_service import CaseAssignmentError, CaseService
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.lawyer import Lawyer
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.user import User
from app.scheduler.scheduler import AppScheduler
from app.security.access_control import (
    ROLE_ADMIN,
    decode_access_token,
    has_role,
)
from app.system.settings_service import SettingsService

router = APIRouter(prefix="/admin", tags=["admin"])


def check(token: str | None) -> dict:
    payload = decode_access_token(token)
    if not payload or not has_role(payload.get("roles"), ROLE_ADMIN):
        raise HTTPException(status_code=401, detail="bad token")
    try:
        payload["uid"] = int(payload.get("uid", 0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="bad token") from exc
    return payload


def _outcome_value(payment: Payment) -> str | None:
    outcome = payment.processing_outcome
    return outcome.value if outcome is not None else None


def _payment_payload(payment: Payment) -> dict:
    return {
        "id": payment.id,
        "case_id": payment.case_id,
        "code": payment.payment_code,
        "title": payment.title,
        "amount": float(payment.amount),
        "currency": payment.currency,
        "status": payment.status,
        "provider": payment.provider,
        "provider_payment_id": payment.provider_payment_id,
        "processing_outcome": _outcome_value(payment),
        "manual_review_required": payment.manual_review_required,
        "processing_error": payment.processing_error,
        "processed_at": (
            payment.processed_at.isoformat() if payment.processed_at else None
        ),
    }


@router.get("/dashboard")
async def dashboard(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check(x_admin_token)
    return await AdminDashboardService(db).build()


@router.get("/settings")
async def settings_list(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check(x_admin_token)
    items = await SettingsService(db).list_settings()
    return [
        {
            "key": item.key,
            "title": item.title,
            "value": item.value,
            "editable": item.is_editable_in_admin,
        }
        for item in items
    ]


@router.post("/settings/{key:path}")
async def set_setting(
    key: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    admin = check(x_admin_token)
    setting = await SettingsService(db).set_value(
        key=key,
        value=payload.get("value"),
        actor_id=admin["uid"],
    )
    await db.commit()
    return {"key": setting.key, "value": setting.value}


@router.get("/cases")
async def cases(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check(x_admin_token)
    result = await db.execute(
        select(Case).order_by(Case.created_at.desc()).limit(200)
    )
    return [
        {
            "id": case.id,
            "number": case.case_number,
            "route": case.route,
            "status": case.status,
            "lawyer_id": case.assigned_lawyer_id,
            "next_action": case.next_action,
        }
        for case in result.scalars().all()
    ]


@router.get("/queue")
async def queue(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check(x_admin_token)
    result = await db.execute(
        select(Case)
        .where(Case.assigned_lawyer_id.is_(None))
        .where(Case.status.notin_(CaseService.CLOSED_STATUSES))
        .order_by(Case.created_at.asc())
        .limit(100)
    )
    return [
        {
            "id": case.id,
            "number": case.case_number,
            "route": case.route,
            "status": case.status,
            "next_action": case.next_action,
        }
        for case in result.scalars().all()
    ]


@router.get("/consultations/pending-confirmation")
async def pending_consultation_confirmations(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Operational queue of paid M2 consultations awaiting a lawyer."""

    check(x_admin_token)
    rows = (
        await db.execute(
            select(Consultation, Case, Lawyer)
            .join(Case, Case.id == Consultation.case_id)
            .outerjoin(Lawyer, Lawyer.id == Consultation.lawyer_id)
            .where(
                Consultation.status
                == ConsultationStatus.PAID_PENDING_CONFIRMATION.value
            )
            .order_by(
                Consultation.scheduled_at.asc(),
                Consultation.created_at.asc(),
                Consultation.id.asc(),
            )
            .limit(200)
        )
    ).all()
    return [
        {
            "id": consultation.id,
            "consultation_id": consultation.id,
            "case_id": case.id,
            "case_number": case.case_number,
            "case_title": case.title,
            "case_status": case.status,
            "case_assigned_lawyer_id": case.assigned_lawyer_id,
            "lawyer_id": lawyer.id if lawyer else consultation.lawyer_id,
            "lawyer_name": lawyer.full_name if lawyer else None,
            "lawyer_email": lawyer.email if lawyer else None,
            "lawyer_active": lawyer.is_active if lawyer else False,
            "integrity_issue": (
                "lawyer_missing"
                if lawyer is None
                else "lawyer_inactive"
                if not lawyer.is_active
                else "case_assignment_mismatch"
                if case.assigned_lawyer_id not in {None, lawyer.id}
                else None
            ),
            "scheduled_at": (
                consultation.scheduled_at.isoformat()
                if consultation.scheduled_at
                else None
            ),
            "consultation_type": consultation.consultation_type,
            "client_description": consultation.client_description,
            "created_at": (
                consultation.created_at.isoformat()
                if consultation.created_at
                else None
            ),
        }
        for consultation, case, lawyer in rows
    ]


@router.get("/lawyers")
async def lawyers(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check(x_admin_token)
    result = await db.execute(select(Lawyer).order_by(Lawyer.id.asc()))
    return [
        {
            "id": lawyer.id,
            "full_name": lawyer.full_name,
            "email": lawyer.email,
            "is_active": lawyer.is_active,
            "workload_limit": lawyer.workload_limit,
        }
        for lawyer in result.scalars().all()
    ]


@router.post("/lawyers")
async def create_lawyer(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check(x_admin_token)
    full_name = str(payload.get("full_name") or "").strip()
    if not full_name:
        raise HTTPException(400, "full_name required")
    try:
        workload_limit = int(payload.get("workload_limit", 30))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "invalid workload_limit") from exc
    if workload_limit < 1:
        raise HTTPException(400, "workload_limit must be positive")

    lawyer = Lawyer(
        full_name=full_name,
        phone=payload.get("phone"),
        email=payload.get("email"),
        specialization=payload.get("specialization"),
        workload_limit=workload_limit,
        is_active=bool(payload.get("is_active", True)),
    )
    db.add(lawyer)
    await db.commit()
    return {"id": lawyer.id, "full_name": lawyer.full_name}


@router.post("/cases/{case_id}/assign/{lawyer_id}")
async def assign(
    case_id: int,
    lawyer_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    admin = check(x_admin_token)
    case = (
        await db.execute(select(Case).where(Case.id == case_id))
    ).scalars().first()
    if not case:
        raise HTTPException(404, "case not found")
    try:
        assigned_case = await CaseService(db).assign_lawyer(
            case=case,
            lawyer_id=lawyer_id,
            actor_id=admin["uid"],
        )
    except CaseAssignmentError as exc:
        raise HTTPException(409, str(exc)) from exc
    await db.commit()
    return {
        "ok": True,
        "case_id": assigned_case.id,
        "lawyer_id": assigned_case.assigned_lawyer_id,
    }


@router.post("/cases/{case_id}/auto-assign")
async def auto_assign(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    admin = check(x_admin_token)
    case = (
        await db.execute(select(Case).where(Case.id == case_id))
    ).scalars().first()
    if not case:
        raise HTTPException(404, "case not found")
    try:
        lawyer = await AssignmentEngine(db).assign_best_lawyer(
            case=case,
            actor_id=admin["uid"],
        )
    except CaseAssignmentError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not lawyer:
        return {"ok": False, "message": "Нет доступных юристов"}
    await db.commit()
    return {"ok": True, "lawyer_id": lawyer.id, "lawyer": lawyer.full_name}


@router.post("/scheduler/run-once")
async def scheduler_run_once(x_admin_token: str | None = Header(default=None)):
    check(x_admin_token)
    return await AppScheduler().run_once()


@router.get("/notifications")
async def notifications(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check(x_admin_token)
    result = await db.execute(
        select(Notification).order_by(Notification.created_at.desc()).limit(100)
    )
    return [
        {
            "id": notification.id,
            "case_id": notification.case_id,
            "event": notification.event_code,
            "title": notification.title,
            "status": notification.status,
            "text": notification.text,
        }
        for notification in result.scalars().all()
    ]


@router.get("/cases/{case_id}")
async def case_detail(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check(x_admin_token)
    case = (
        await db.execute(select(Case).where(Case.id == case_id))
    ).scalars().first()
    if not case:
        raise HTTPException(404, "case not found")
    client = (
        await db.execute(select(User).where(User.id == case.client_id))
    ).scalars().first()
    payments = (
        await db.execute(
            select(Payment)
            .where(Payment.case_id == case.id)
            .order_by(Payment.created_at.desc(), Payment.id.desc())
        )
    ).scalars().all()
    documents = (
        await db.execute(select(Document).where(Document.case_id == case.id))
    ).scalars().all()
    return {
        "case": {
            "id": case.id,
            "number": case.case_number,
            "route": case.route,
            "status": case.status,
            "next_action": case.next_action,
            "lawyer_id": case.assigned_lawyer_id,
        },
        "client": (
            {
                "id": client.id,
                "telegram_id": client.telegram_id,
                "name": client.full_name,
                "username": client.telegram_username,
            }
            if client
            else None
        ),
        "payments": [_payment_payload(payment) for payment in payments],
        "documents": [
            {
                "id": document.id,
                "type": document.document_type,
                "title": document.title,
                "file_name": document.file_name,
                "status": document.status,
                "version": document.version,
            }
            for document in documents
        ],
    }


@router.post("/cases/{case_id}/status")
async def manual_status(
    case_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    admin = check(x_admin_token)
    next_status = payload.get("status")
    valid_statuses = {status.value for status in CaseStatus}
    if not next_status:
        raise HTTPException(400, "status required")
    if next_status not in valid_statuses:
        raise HTTPException(400, "unknown status")
    case = (
        await db.execute(select(Case).where(Case.id == case_id))
    ).scalars().first()
    if not case:
        raise HTTPException(404, "case not found")
    await CaseService(db).change_status(
        case=case,
        next_status=next_status,
        actor_type="admin",
        actor_id=admin["uid"],
        force=True,
        comment=(
            payload.get("comment")
            or "Ручное изменение статуса администратором"
        ),
    )
    await db.commit()
    return {
        "ok": True,
        "case_id": case.id,
        "status": case.status,
        "route": case.route,
        "next_action": case.next_action,
    }


@router.post("/payments/{payment_id}/confirm")
async def manual_confirm_payment(
    payment_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    admin = check(x_admin_token)
    payment = (
        await db.execute(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        )
    ).scalars().first()
    if not payment:
        raise HTTPException(404, "payment not found")
    case = (
        await db.execute(
            select(Case).where(Case.id == payment.case_id).with_for_update()
        )
    ).scalars().first()
    if not case:
        raise HTTPException(404, "case not found")

    await PaymentWebhookService(db).process_successful_payment(
        payment=payment,
        case=case,
        provider_payload={
            "source": "admin_manual_confirm",
            "admin_user_id": admin["uid"],
            "admin_username": admin.get("username"),
        },
        allow_reprocess=True,
        actor_type="admin",
        actor_id=admin["uid"],
        source="admin_manual_confirm",
    )
    await db.commit()

    response = {
        "ok": not payment.manual_review_required,
        "payment_id": payment.id,
        "status": payment.status,
        "case_status": case.status,
        "processing_outcome": _outcome_value(payment),
        "manual_review_required": payment.manual_review_required,
        "processing_error": payment.processing_error,
    }
    if payment.manual_review_required:
        raise HTTPException(status_code=409, detail=response)
    return response


@router.get("/payments")
async def all_payments(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check(x_admin_token)
    result = await db.execute(
        select(Payment).order_by(Payment.created_at.desc()).limit(200)
    )
    return [_payment_payload(payment) for payment in result.scalars().all()]


@router.get("/documents")
async def all_documents(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check(x_admin_token)
    result = await db.execute(
        select(Document).order_by(Document.created_at.desc()).limit(200)
    )
    return [
        {
            "id": document.id,
            "case_id": document.case_id,
            "type": document.document_type,
            "title": document.title,
            "file_name": document.file_name,
            "status": document.status,
            "version": document.version,
        }
        for document in result.scalars().all()
    ]


@router.get("/statuses")
async def statuses(x_admin_token: str | None = Header(default=None)):
    check(x_admin_token)
    return [status.value for status in CaseStatus]
