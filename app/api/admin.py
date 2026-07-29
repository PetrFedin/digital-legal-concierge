from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.admin_dashboard import AdminDashboardService
from app.config import settings
from app.db.session import get_db
from app.domain.cases.assignment_service import CaseAssignmentService
from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.document import Document
from app.models.lawyer import Lawyer
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.user import User
from app.scheduler.scheduler import AppScheduler
from app.security.access_control import ROLE_ADMIN, decode_access_token, has_role
from app.system.settings_service import SettingsService

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(token: str | None) -> dict:
    payload = decode_access_token(token)
    if not payload or not has_role(payload.get("roles"), ROLE_ADMIN):
        raise HTTPException(
            status_code=403,
            detail="Доступ только для администратора",
        )
    return payload


def actor_id_from_token(payload: dict) -> int | None:
    try:
        actor_id = int(payload.get("uid") or 0)
    except (TypeError, ValueError):
        actor_id = 0
    return actor_id or None


def manual_payment_confirmation_enabled() -> bool:
    return settings.payment_provider == "fake" and (
        settings.app_env in {"local", "test"} or settings.demo_mode
    )


def payment_can_be_manually_confirmed(payment: Payment) -> bool:
    return (
        manual_payment_confirmation_enabled()
        and payment.provider in {None, "fake"}
        and payment.status
        in {
            PaymentStatus.PENDING,
            PaymentStatus.WAITING_CONFIRMATION,
        }
    )


@router.get("/dashboard")
async def dashboard(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    return await AdminDashboardService(db).build()


@router.get("/settings")
async def settings_list(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
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
    actor = require_admin(x_admin_token)
    setting = await SettingsService(db).set_value(
        key=key,
        value=payload.get("value"),
        actor_id=actor_id_from_token(actor) or 0,
    )
    await db.commit()
    return {"key": setting.key, "value": setting.value}


@router.get("/cases")
async def cases(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
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
    require_admin(x_admin_token)
    result = await db.execute(
        select(Case)
        .where(Case.assigned_lawyer_id.is_(None))
        .where(Case.status.notin_(["M1_CLOSED", "M2_CLOSED", "ARCHIVED"]))
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


@router.get("/lawyers")
async def lawyers(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
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
    require_admin(x_admin_token)
    full_name = str(payload.get("full_name") or "").strip()
    if not full_name:
        raise HTTPException(status_code=400, detail="ФИО юриста обязательно")
    try:
        workload_limit = int(payload.get("workload_limit", 30))
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail="Некорректный лимит дел") from error
    if workload_limit < 1 or workload_limit > 500:
        raise HTTPException(
            status_code=400,
            detail="Лимит активных дел должен быть от 1 до 500",
        )
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
    actor = require_admin(x_admin_token)
    try:
        case = await CaseAssignmentService(db).assign_case(
            case_id=case_id,
            lawyer_id=lawyer_id,
            actor_type="admin",
            actor_id=actor_id_from_token(actor),
            comment="Ручное назначение из административной панели",
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "ok": True,
        "case_id": case.id,
        "lawyer_id": case.assigned_lawyer_id,
    }


@router.post("/cases/{case_id}/auto-assign")
async def auto_assign(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = require_admin(x_admin_token)
    try:
        case = await CaseAssignmentService(db).auto_assign_case(
            case_id=case_id,
            actor_type="admin",
            actor_id=actor_id_from_token(actor),
            comment="Автоматическое назначение из административной панели",
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    if not case:
        return {"ok": False, "message": "Нет доступных юристов"}
    lawyer = await db.get(Lawyer, case.assigned_lawyer_id)
    return {
        "ok": True,
        "lawyer_id": case.assigned_lawyer_id,
        "lawyer": lawyer.full_name if lawyer else None,
    }


@router.post("/scheduler/run-once")
async def scheduler_run_once(
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    return await AppScheduler().run_once()


@router.get("/notifications")
async def notifications(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
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
    require_admin(x_admin_token)
    case = await db.get(Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")
    client = await db.get(User, case.client_id)
    payments = (
        await db.execute(select(Payment).where(Payment.case_id == case.id))
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
        "payments": [
            {
                "id": payment.id,
                "code": payment.payment_code,
                "title": payment.title,
                "amount": float(payment.amount),
                "status": payment.status,
                "provider": payment.provider,
                "manual_confirm_allowed": payment_can_be_manually_confirmed(payment),
            }
            for payment in payments
        ],
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
    actor = require_admin(x_admin_token)
    next_status = payload.get("status")
    if not next_status:
        raise HTTPException(status_code=400, detail="status required")
    case = await db.get(Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")
    await CaseService(db).change_status(
        case=case,
        next_status=next_status,
        actor_type="admin",
        actor_id=actor_id_from_token(actor),
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
    actor = require_admin(x_admin_token)
    if not manual_payment_confirmation_enabled():
        raise HTTPException(
            status_code=403,
            detail=(
                "Ручное подтверждение платежей отключено. "
                "В production статус должен поступать только от провайдера."
            ),
        )
    payment = await db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="payment not found")
    if not payment_can_be_manually_confirmed(payment):
        raise HTTPException(
            status_code=409,
            detail=(
                "Этот платёж нельзя подтвердить вручную: "
                "провайдер или статус не соответствует тестовому сценарию"
            ),
        )
    case = await db.get(Case, payment.case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    old_status = payment.status
    await PaymentWebhookService(db).process_successful_payment(
        payment=payment,
        case=case,
        provider_payload={
            "source": "admin_fake_manual_confirm",
            "actor_id": actor_id_from_token(actor),
        },
    )
    await add_case_history_event(
        db,
        actor_type="admin",
        actor_id=actor_id_from_token(actor),
        case_id=case.id,
        action="ADMIN_FAKE_PAYMENT_CONFIRMED",
        old_value={
            "payment_id": payment.id,
            "status": old_status,
        },
        new_value={
            "payment_id": payment.id,
            "status": payment.status,
        },
        comment="Тестовое ручное подтверждение платежа в административной панели",
    )
    await db.commit()
    return {
        "ok": True,
        "payment_id": payment.id,
        "status": payment.status,
        "case_status": case.status,
    }


@router.get("/payments")
async def all_payments(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    result = await db.execute(
        select(Payment).order_by(Payment.created_at.desc()).limit(200)
    )
    return [
        {
            "id": payment.id,
            "case_id": payment.case_id,
            "code": payment.payment_code,
            "title": payment.title,
            "amount": float(payment.amount),
            "status": payment.status,
            "provider": payment.provider,
            "manual_confirm_allowed": payment_can_be_manually_confirmed(payment),
        }
        for payment in result.scalars().all()
    ]


@router.get("/documents")
async def all_documents(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
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
async def statuses(
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    return [status.value for status in CaseStatus]
