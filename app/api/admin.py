from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.admin_dashboard import AdminDashboardService
from app.config import settings
from app.db.session import get_db
from app.domain.cases.assignment_service import CaseAssignmentService
from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.cases.case_transition_policy import CaseTransitionError
from app.domain.cases.enforcement_service import EnforcementError, EnforcementService
from app.domain.cases.m1_process_service import M1ProcessError, M1ProcessService
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.m1_offline_receipt_service import (
    M1OfflineReceiptError,
    M1OfflineReceiptService,
    offline_receipt_unavailable_reason,
)
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
from app.security.current_admin import current_admin_gateway
from app.security.current_admin import require_current_settings_admin
from app.security.current_admin import require_current_staff_token
from app.system.settings_service import SettingsService

router = APIRouter(
    prefix="/admin", tags=["admin"], dependencies=[Depends(current_admin_gateway)],
)


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


def admin_payment_row(payment: Payment, case: Case | None) -> dict:
    """Financial details and the exact snapshots required by the receipt form."""
    return {
        "id": payment.id,
        "case_id": payment.case_id,
        "code": payment.payment_code,
        "title": payment.title,
        "amount": float(payment.amount),
        "amount_exact": str(payment.amount),
        "currency": payment.currency,
        "status": payment.status,
        "provider": payment.provider,
        "updated_at": payment.updated_at.isoformat(),
        "case_status": case.status if case else None,
        "case_updated_at": case.updated_at.isoformat() if case else None,
        "manual_confirm_allowed": payment_can_be_manually_confirmed(payment),
        "offline_receipt_allowed": offline_receipt_unavailable_reason(payment, case) is None,
        "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
        "confirmed_by": payment.confirmed_by,
        "receipt_reference": payment.receipt_reference,
        "confirmation_reason": payment.confirmation_reason,
    }


class M1OfflineReceiptPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    amount: Decimal = Field(gt=Decimal("0"), max_digits=14, decimal_places=2)
    paid_at: AwareDatetime
    reference: str = Field(min_length=1, max_length=255, strict=True)
    reason: str = Field(min_length=5, max_length=2000, strict=True)
    case_id: int = Field(gt=0, strict=True)
    expected_payment_status: str = Field(min_length=1, max_length=100, strict=True)
    expected_payment_updated_at: str = Field(min_length=1, max_length=80, strict=True)
    expected_case_status: str = Field(min_length=1, max_length=100, strict=True)
    expected_case_updated_at: str = Field(min_length=1, max_length=80, strict=True)


@router.get("/dashboard")
async def dashboard(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    return await AdminDashboardService(db).build()


@router.get("/settings")
async def settings_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await require_current_settings_admin(request, db, x_admin_token)
    items = await SettingsService(db).list_settings()
    return [
        {
            "key": item.key,
            "title": item.title,
            "value": item.value,
            "editable": item.is_editable_in_admin,
            "updated_at": item.updated_at.isoformat(),
        }
        for item in items
    ]


@router.post("/settings/{key:path}")
async def set_setting(
    key: str,
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await require_current_settings_admin(request, db, x_admin_token)
    try:
        setting = await SettingsService(db).set_value(
            key=key,
            value=payload.get("value"),
            actor_id=actor_id_from_token(actor) or 0,
            expected_updated_at=payload.get("expected_updated_at"),
        )
        await db.commit()
        await db.refresh(setting)
        return {
            "key": setting.key,
            "value": setting.value,
            "updated_at": setting.updated_at.isoformat(),
        }
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise


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
    email = str(payload.get("email") or "").strip().lower()
    if not full_name:
        raise HTTPException(status_code=400, detail="ФИО юриста обязательно")
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Укажите корректный email юриста")
    try:
        workload_limit = int(payload.get("workload_limit", 30))
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail="Некорректный лимит дел") from error
    if workload_limit < 1 or workload_limit > 500:
        raise HTTPException(
            status_code=400,
            detail="Лимит активных дел должен быть от 1 до 500",
        )
    try:
        duplicate = (
            await db.execute(
                select(Lawyer).where(Lawyer.email == email).with_for_update()
            )
        ).scalar_one_or_none()
        if duplicate:
            raise HTTPException(409, "Юрист с таким email уже существует")
        lawyer = Lawyer(
            full_name=full_name,
            phone=payload.get("phone"),
            email=email,
            specialization=payload.get("specialization"),
            workload_limit=workload_limit,
            is_active=bool(payload.get("is_active", True)),
        )
        db.add(lawyer)
        await db.flush()
        await db.commit()
        return {
            "ok": True,
            "id": lawyer.id,
            "full_name": lawyer.full_name,
            "email": lawyer.email,
        }
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


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
    except Exception:
        await db.rollback()
        raise
    return {
        "ok": True,
        "case_id": case.id,
        "lawyer_id": case.assigned_lawyer_id,
    }


@router.post("/cases/{case_id}/auto-assign")
async def auto_assign(
    case_id: int,
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = require_admin(x_admin_token)
    snapshot = payload or {}
    try:
        case = await CaseAssignmentService(db).auto_assign_case(
            case_id=case_id,
            actor_type="admin",
            actor_id=actor_id_from_token(actor),
            comment="Автоматическое назначение из административной панели",
            expected_lawyer_id=snapshot.get("expected_lawyer_id"),
            expected_status=snapshot.get("expected_status"),
        )
        if not case:
            raise ValueError("Нет доступных юристов с оставшейся ёмкостью")
        await db.commit()
        lawyer = await db.get(Lawyer, case.assigned_lawyer_id)
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise
    return {
        "ok": True,
        "case_id": case.id,
        "status": case.status,
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
            "enforcement_number": case.enforcement_number,
            "enforcement_status": case.enforcement_status,
            "enforcement_started_at": (
                case.enforcement_started_at.isoformat()
                if case.enforcement_started_at
                else None
            ),
            "received_amount": (
                str(case.received_amount) if case.received_amount is not None else None
            ),
            "received_at": case.received_at.isoformat() if case.received_at else None,
            "success_fee_amount": (
                str(case.success_fee_amount)
                if case.success_fee_amount is not None
                else None
            ),
            "updated_at": case.updated_at.isoformat(),
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
        "payments": [admin_payment_row(payment, case) for payment in payments],
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
    comment = str(payload.get("comment") or "").strip()
    if len(comment) < 5:
        raise HTTPException(
            status_code=400,
            detail="Комментарий к ручному изменению должен содержать не менее 5 символов",
        )
    try:
        case = (
            await db.execute(
                select(Case).where(Case.id == case_id).with_for_update()
            )
        ).scalar_one_or_none()
        if not case:
            raise HTTPException(status_code=404, detail="case not found")
        expected_status = payload.get("expected_status")
        if expected_status is not None and str(case.status) != str(expected_status):
            raise HTTPException(
                status_code=409,
                detail="Статус дела изменился после загрузки экрана. Обновите карточку",
            )
        await CaseService(db).change_status(
            case=case,
            next_status=next_status,
            actor_type="admin",
            actor_id=actor_id_from_token(actor),
            force=True,
            comment=comment,
        )
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except CaseTransitionError as error:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise
    return {
        "ok": True,
        "case_id": case.id,
        "status": case.status,
        "route": case.route,
        "next_action": case.next_action,
    }


@router.post("/cases/{case_id}/close")
async def admin_close_case(
    case_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = require_admin(x_admin_token)
    reason = str(payload.get("reason") or "").strip()
    expected_status = payload.get("expected_status")
    expected_updated_at = payload.get("expected_updated_at")
    try:
        case = (
            await db.execute(
                select(Case).where(Case.id == case_id).with_for_update()
            )
        ).scalar_one_or_none()
        if not case:
            raise HTTPException(status_code=404, detail="case not found")
        if expected_status is not None and str(case.status) != str(expected_status):
            raise HTTPException(
                status_code=409,
                detail="Статус дела изменился после загрузки экрана. Обновите карточку",
            )
        if (
            expected_updated_at is not None
            and case.updated_at.isoformat() != str(expected_updated_at)
        ):
            raise HTTPException(
                status_code=409,
                detail="Дело изменилось после загрузки экрана. Обновите карточку",
            )
        await M1ProcessService(db).close_after_success_fee(
            case=case,
            actor_type="admin",
            actor_id=actor_id_from_token(actor),
            reason=reason,
        )
        await db.commit()
        await db.refresh(case)
    except HTTPException:
        await db.rollback()
        raise
    except (M1ProcessError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise
    return {
        "ok": True,
        "case_id": case.id,
        "status": case.status,
        "closure_reason": case.closure_reason,
        "updated_at": case.updated_at.isoformat(),
    }


@router.post("/cases/{case_id}/enforcement/receipt")
async def admin_record_enforcement_receipt(
    case_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = require_admin(x_admin_token)
    final = payload.get("final") is True
    try:
        case = (
            await db.execute(
                select(Case).where(Case.id == case_id).with_for_update()
            )
        ).scalar_one_or_none()
        if not case:
            raise HTTPException(status_code=404, detail="case not found")
        expected_status = payload.get("expected_status")
        if expected_status is not None and str(case.status) != str(expected_status):
            raise HTTPException(
                status_code=409,
                detail="Статус дела изменился после загрузки экрана. Обновите карточку",
            )
        expected_updated_at = payload.get("expected_updated_at")
        if (
            expected_updated_at is not None
            and case.updated_at.isoformat() != str(expected_updated_at)
        ):
            raise HTTPException(
                status_code=409,
                detail="Дело изменилось после загрузки экрана. Обновите карточку",
            )
        source_version = case.updated_at.isoformat()
        await EnforcementService(db).record_receipt(
            case=case,
            amount=payload.get("amount"),
            final=final,
            actor_type="admin",
            actor_id=actor_id_from_token(actor),
            comment=str(payload.get("comment") or "").strip() or None,
        )
        if final:
            await NotificationEngine(db).emit(
                event_code="M1_MONEY_RECEIVED",
                case_id=case.id,
                payload={
                    "case_number": case.case_number,
                    "amount": str(case.received_amount),
                },
                dedupe_key=f"case:{case.id}:money-received:{source_version}",
            )
        await db.commit()
        await db.refresh(case)
    except HTTPException:
        await db.rollback()
        raise
    except (EnforcementError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise
    return {
        "ok": True,
        "case_id": case.id,
        "status": case.status,
        "received_amount": str(case.received_amount),
        "received_at": case.received_at.isoformat() if case.received_at else None,
        "final": final,
        "updated_at": case.updated_at.isoformat(),
    }


@router.post("/payments/{payment_id}/confirm")
async def manual_confirm_payment(
    payment_id: int,
    payload: dict | None = None,
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
    snapshot = payload or {}
    try:
        payment = (
            await db.execute(
                select(Payment)
                .where(Payment.id == payment_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not payment:
            raise HTTPException(status_code=404, detail="payment not found")
        expected_status = snapshot.get("expected_status")
        if expected_status is not None and str(payment.status) != str(expected_status):
            raise HTTPException(
                status_code=409,
                detail="Статус платежа изменился после загрузки экрана. Обновите список",
            )
        if not payment_can_be_manually_confirmed(payment):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Этот платёж нельзя подтвердить вручную: "
                    "провайдер или статус не соответствует тестовому сценарию"
                ),
            )
        case = (
            await db.execute(
                select(Case)
                .where(Case.id == payment.case_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
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
            "case_id": case.id,
            "case_status": case.status,
        }
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post("/payments/{payment_id}/record-receipt")
async def record_payment_receipt(
    payment_id: int,
    payload: M1OfflineReceiptPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    token = x_admin_token or request.cookies.get(settings.admin_session_cookie)
    actor = await require_current_staff_token(
        db, token, allowed_roles={ROLE_ADMIN}, allow_test_legacy=False,
    )
    try:
        payment, case = await M1OfflineReceiptService(db).record_receipt(
            payment_id=payment_id,
            actor_id=int(actor["uid"]),
            **payload.model_dump(),
        )
        await db.commit()
        return {
            "ok": True,
            "payment_id": payment.id,
            "status": payment.status,
            "case_id": case.id,
            "case_status": case.status,
            "amount": str(payment.amount),
            "paid_at": payment.paid_at.isoformat(),
            "confirmed_by": payment.confirmed_by,
        }
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (M1OfflineReceiptError, CaseTransitionError) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise


@router.get("/payments")
async def all_payments(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    result = await db.execute(
        select(Payment, Case)
        .outerjoin(Case, Case.id == Payment.case_id)
        .order_by(Payment.created_at.desc()).limit(200)
    )
    return [
        admin_payment_row(payment, case)
        for payment, case in result.all()
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
