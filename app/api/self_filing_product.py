from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.cases.case_responsibility import lawyer_can_access_case
from app.domain.cases.self_filing_documents import (
    SELF_FILING_DELIVERABLE_FIELDS,
    SELF_FILING_DELIVERABLE_TYPES,
    publish_self_filing_package,
)
from app.domain.cases.self_filing_email_sender import (
    SelfFilingEmailSender,
    email_delivery_configuration_error,
    email_delivery_configured,
)
from app.domain.cases.self_filing_readiness import self_filing_readiness
from app.domain.cases.self_filing_service import (
    EMAIL_FAILED,
    EMAIL_QUEUED,
    EMAIL_SENT,
    JURISDICTION_BASES,
    SelfFilingError,
    SelfFilingService,
)
from app.domain.cases.service_modes import M1ServiceMode
from app.domain.documents.document_service import DuplicateDocumentError
from app.domain.payments.bank_requisites import bank_requisites_snapshot
from app.domain.documents.staff_upload_storage import save_staff_upload
from app.domain.statuses.case_statuses import CaseStatus
from app.models.calculation import Calculation
from app.models.case import Case
from app.models.document import Document
from app.models.payment import Payment
from app.models.self_filing_package import SelfFilingPackage
from app.models.user import User
from app.security.access_control import ROLE_ADMIN, ROLE_LAWYER, ROLE_SUPERADMIN
from app.security.document_access import DocumentActor, resolve_document_actor
from app.security.file_uploads import UploadSecurityError
from app.security.lawyer_access import require_lawyer_actor
from app.storage import LocalStorageService

router = APIRouter(prefix="/self-filing", tags=["self-filing"])


SELF_FILING_STATUS_LABELS = {
    CaseStatus.M1_SELF_FILING_PROFILE_PENDING.value: "Нужны данные клиента для пакета",
    CaseStatus.M1_SELF_FILING_DOCUMENTS_PENDING.value: "Клиент собирает документы",
    CaseStatus.M1_SELF_FILING_DOCUMENTS_RECEIVED.value: "Документы переданы юридической команде",
    CaseStatus.M1_SELF_FILING_LAWYER_REVIEW.value: "Юрист проверяет комплект и подсудность",
    CaseStatus.M1_SELF_FILING_DOCS_REQUESTED.value: "Запрошены дополнительные документы",
    CaseStatus.M1_SELF_FILING_PAYMENT_PENDING.value: "Комплект подтверждён — ожидается оплата 15 000 ₽",
    CaseStatus.M1_SELF_FILING_PREPARATION.value: "Идёт подготовка итогового пакета",
    CaseStatus.M1_SELF_FILING_READY.value: "Итоговый пакет готов к доставке",
    CaseStatus.M1_SELF_FILING_DELIVERED.value: "Пакет доставлен клиенту",
    CaseStatus.M1_SELF_FILING_CLOSED.value: "Услуга подготовки пакета завершена",
}


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _actor(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
) -> DocumentActor:
    return await resolve_document_actor(db, _token(request, header_token))


async def _case_for_staff(
    db: AsyncSession,
    *,
    actor: DocumentActor,
    case_id: int,
) -> tuple[Case, User]:
    row = (
        await db.execute(
            select(Case, User)
            .join(User, User.id == Case.client_id)
            .where(Case.id == int(case_id))
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    case, user = row
    if str(case.service_mode or "") != M1ServiceMode.SELF_FILING_PACKAGE.value:
        raise HTTPException(
            status_code=409,
            detail="Для обращения не выбран пакет самостоятельной подачи",
        )
    if actor.role == ROLE_LAWYER and not await lawyer_can_access_case(
        db,
        case=case,
        lawyer_id=actor.lawyer_id,
    ):
        raise HTTPException(
            status_code=403,
            detail="Обращение не относится к ответственности текущего юриста",
        )
    if actor.role not in {ROLE_LAWYER, ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return case, user


async def _lawyer_case(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
    *,
    case_id: int,
):
    token = _token(request, header_token)
    actor = await require_lawyer_actor(db, token)
    case = await db.get(Case, int(case_id))
    if case is None:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    if str(case.service_mode or "") != M1ServiceMode.SELF_FILING_PACKAGE.value:
        raise HTTPException(
            status_code=409,
            detail="Для обращения не выбран пакет самостоятельной подачи",
        )
    if not await lawyer_can_access_case(
        db,
        case=case,
        lawyer_id=int(actor.lawyer.id),
    ):
        raise HTTPException(
            status_code=403,
            detail="Обращение не относится к ответственности текущего юриста",
        )
    return actor, case


def _expect_version(package: SelfFilingPackage, payload: dict) -> None:
    try:
        expected = int(payload.get("expected_package_version"))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=409,
            detail="Экран устарел: обновите карточку перед действием",
        )
    if expected != int(package.version or 0):
        raise HTTPException(
            status_code=409,
            detail="Карточка пакета уже изменилась. Обновите данные и повторите решение.",
        )


def _document_payload(document: Document) -> dict:
    return {
        "id": int(document.id),
        "type": document.document_type,
        "title": document.title,
        "file_name": document.file_name,
        "status": str(document.status),
        "version": int(document.version or 1),
        "sha256": document.sha256,
        "sha256_prefix": str(document.sha256 or "")[:12],
        "lawyer_comment": document.lawyer_comment,
        "updated_at": document.updated_at.isoformat() if document.updated_at else None,
    }


@router.get("/readiness")
async def self_filing_runtime_readiness(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _actor(request, db, x_admin_token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(
            status_code=403,
            detail="Проверка готовности услуги доступна только администратору",
        )
    result = await self_filing_readiness(db)
    return {
        **result,
        "actor_role": actor.role,
        "claim_limit": (
            "Статическая проверка не подтверждает фактическую доставку письма. "
            "Production-ready остаётся false до отдельного контролируемого "
            "SMTP-подтверждения."
        ),
    }


@router.get("/cases/{case_id}")
async def self_filing_context(
    case_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _actor(request, db, x_admin_token)
    case, user = await _case_for_staff(db, actor=actor, case_id=case_id)
    package = await SelfFilingService(db).require_package(case_id=case.id)
    documents = list(
        (
            await db.execute(
                select(Document)
                .where(Document.case_id == int(case.id))
                .order_by(Document.created_at.asc(), Document.id.asc())
            )
        ).scalars().all()
    )
    payments = list(
        (
            await db.execute(
                select(Payment)
                .where(Payment.case_id == int(case.id))
                .order_by(Payment.created_at.asc(), Payment.id.asc())
            )
        ).scalars().all()
    )
    can_mutate = bool(
        actor.role == ROLE_LAWYER
        and await lawyer_can_access_case(
            db,
            case=case,
            lawyer_id=actor.lawyer_id,
        )
    )
    claim_calculation = (
        await db.get(Calculation, int(package.claim_source_calculation_id))
        if package.claim_source_calculation_id
        else None
    )
    return {
        "case": {
            "id": int(case.id),
            "number": case.case_number,
            "status": str(case.status),
            "status_label": SELF_FILING_STATUS_LABELS.get(
                str(case.status),
                "Статус требует уточнения",
            ),
            "next_action": case.next_action,
            "service_mode": case.service_mode,
            "assigned_lawyer_id": case.assigned_lawyer_id,
            "updated_at": case.updated_at.isoformat() if case.updated_at else None,
        },
        "client": {
            "id": int(user.id),
            "name": user.full_name,
        },
        "package": {
            "id": int(package.id),
            "status": package.status,
            "version": int(package.version or 1),
            "region": package.client_region,
            "address": package.client_address,
            "delivery_email": package.delivery_email,
            "email_confirmed_at": (
                package.email_confirmed_at.isoformat()
                if package.email_confirmed_at
                else None
            ),
            "email_verified": package.email_confirmed_at is not None,
            "email_verification_pending": bool(
                package.email_confirmed_at is None
                and package.email_verification_hash
                and package.email_verification_expires_at
            ),
            "email_verification_sent_at": (
                package.email_verification_sent_at.isoformat()
                if package.email_verification_sent_at
                else None
            ),
            "email_verification_expires_at": (
                package.email_verification_expires_at.isoformat()
                if package.email_verification_expires_at
                else None
            ),
            "email_verification_attempts": int(
                package.email_verification_attempts or 0
            ),
            "documents_complete_at": (
                package.documents_complete_at.isoformat()
                if package.documents_complete_at
                else None
            ),
            "court_name": package.court_name,
            "court_address": package.court_address,
            "jurisdiction_basis": package.jurisdiction_basis,
            "jurisdiction_note": package.jurisdiction_note,
            "transfer_act_signed": package.transfer_act_signed,
            "transfer_act_date": (
                package.transfer_act_date.isoformat()
                if package.transfer_act_date
                else None
            ),
            "transfer_act_confirmed_at": (
                package.transfer_act_confirmed_at.isoformat()
                if package.transfer_act_confirmed_at
                else None
            ),
            "jurisdiction_confirmed_at": (
                package.jurisdiction_confirmed_at.isoformat()
                if package.jurisdiction_confirmed_at
                else None
            ),
            "payment_confirmed_at": (
                package.payment_confirmed_at.isoformat()
                if package.payment_confirmed_at
                else None
            ),
            "claim_calculation_id": package.claim_source_calculation_id,
            "claim_calculation_cutoff_date": (
                package.claim_calculation_cutoff_date.isoformat()
                if package.claim_calculation_cutoff_date
                else None
            ),
            "claim_calculation_basis": package.claim_calculation_basis,
            "claim_update_in_court_required": package.claim_update_in_court_required,
            "claim_calculation_amount": (
                str(claim_calculation.penalty_amount)
                if claim_calculation and claim_calculation.penalty_amount is not None
                else None
            ),
            "sla_started_at": (
                package.sla_started_at.isoformat() if package.sla_started_at else None
            ),
            "sla_due_at": package.sla_due_at.isoformat() if package.sla_due_at else None,
            "ready_at": package.ready_at.isoformat() if package.ready_at else None,
            "delivered_at": (
                package.delivered_at.isoformat() if package.delivered_at else None
            ),
            "package_document_id": package.package_document_id,
            "deliverables": {
                dtype: getattr(package, SELF_FILING_DELIVERABLE_FIELDS[dtype])
                for dtype in SELF_FILING_DELIVERABLE_TYPES
            },
            "email_delivery_status": package.email_delivery_status,
            "email_delivery_attempts": int(package.email_delivery_attempts or 0),
            "email_message_id": package.email_message_id,
            "email_sent_at": (
                package.email_sent_at.isoformat() if package.email_sent_at else None
            ),
            "email_last_error": package.email_last_error,
        },
        "documents": [_document_payload(item) for item in documents],
        "payments": [
            {
                "id": int(item.id),
                "code": str(item.payment_code),
                "title": item.title,
                "amount": str(item.amount),
                "status": str(item.status),
                "provider": item.provider,
                "payment_purpose": item.payment_purpose,
                "payment_details_snapshot": item.payment_details_snapshot,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            }
            for item in payments
        ],
        "actor": {
            "role": actor.role,
            "lawyer_id": actor.lawyer_id,
            "can_mutate": can_mutate,
            "can_financial_reconcile": actor.role in {ROLE_ADMIN, ROLE_SUPERADMIN},
        },
        "capabilities": {
            "email_delivery_configured": email_delivery_configured(),
            "email_delivery_configuration_error": (
                email_delivery_configuration_error()
            ),
            "jurisdiction_bases": sorted(JURISDICTION_BASES),
            "bank_requisites": bank_requisites_snapshot(),
            "business_timezone": settings.business_timezone,
            "business_timezone_label": settings.business_timezone_label,
        },
    }


@router.post("/cases/{case_id}/review/start")
async def start_self_filing_review(
    case_id: int,
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor, _case = await _lawyer_case(
        request,
        db,
        x_admin_token,
        case_id=case_id,
    )
    try:
        service = SelfFilingService(db)
        package = await service.require_package(case_id=case_id, for_update=True)
        _expect_version(package, payload)
        package = await service.begin_lawyer_review(
            case_id=case_id,
            lawyer_id=int(actor.lawyer.id),
        )
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except (SelfFilingError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"ok": True, "package_version": int(package.version or 1)}


@router.post("/cases/{case_id}/request-documents")
async def request_self_filing_documents(
    case_id: int,
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor, _case = await _lawyer_case(
        request,
        db,
        x_admin_token,
        case_id=case_id,
    )
    try:
        service = SelfFilingService(db)
        package = await service.require_package(case_id=case_id, for_update=True)
        _expect_version(package, payload)
        package = await service.request_more_documents(
            case_id=case_id,
            lawyer_id=int(actor.lawyer.id),
            reason=str(payload.get("reason") or ""),
        )
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except (SelfFilingError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"ok": True, "package_version": int(package.version or 1)}


@router.post("/cases/{case_id}/approve-for-payment")
async def approve_self_filing_for_payment(
    case_id: int,
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor, _case = await _lawyer_case(
        request,
        db,
        x_admin_token,
        case_id=case_id,
    )
    try:
        service = SelfFilingService(db)
        package = await service.require_package(case_id=case_id, for_update=True)
        _expect_version(package, payload)
        raw_act_date = str(payload.get("transfer_act_date") or "").strip()
        try:
            transfer_act_date = (
                date.fromisoformat(raw_act_date)
                if raw_act_date
                else None
            )
        except ValueError as error:
            raise SelfFilingError(
                "Дата акта передачи должна быть в формате ГГГГ-ММ-ДД"
            ) from error
        package, payment = await service.approve_for_payment(
            case_id=case_id,
            lawyer_id=int(actor.lawyer.id),
            court_name=str(payload.get("court_name") or ""),
            court_address=str(payload.get("court_address") or ""),
            jurisdiction_basis=str(payload.get("jurisdiction_basis") or ""),
            jurisdiction_note=str(payload.get("jurisdiction_note") or ""),
            completeness_confirmed=(
                payload.get("completeness_confirmed") is True
            ),
            transfer_act_signed=(
                payload.get("transfer_act_signed") is True
            ),
            transfer_act_date=transfer_act_date,
        )
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except (SelfFilingError, ValueError, RuntimeError) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "ok": True,
        "package_version": int(package.version or 1),
        "payment_id": int(payment.id),
        "amount": str(payment.amount),
        "status": str(payment.status),
    }


@router.post("/cases/{case_id}/payment-review/{payment_id}/resolve")
async def resolve_self_filing_payment_review(
    case_id: int,
    payment_id: int,
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _actor(request, db, x_admin_token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(
            status_code=403,
            detail="Финансовая сверка доступна только администратору",
        )
    case, _user = await _case_for_staff(
        db,
        actor=actor,
        case_id=case_id,
    )
    service = SelfFilingService(db)
    try:
        package = await service.require_package(
            case_id=case.id,
            for_update=True,
        )
        _expect_version(package, payload)
        package, payment = await service.resolve_received_payment_review(
            case_id=int(case.id),
            payment_id=int(payment_id),
            actor_id=int(actor.account_id),
            decision=str(payload.get("decision") or ""),
            comment=str(payload.get("comment") or ""),
        )
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except (SelfFilingError, ValueError, RuntimeError, LookupError) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error

    return {
        "ok": True,
        "case_id": int(case.id),
        "package_version": int(package.version or 1),
        "payment_id": int(payment.id),
        "payment_status": str(payment.status),
        "case_status": str(case.status),
        "sla_started_at": (
            package.sla_started_at.isoformat()
            if package.sla_started_at
            else None
        ),
        "sla_due_at": (
            package.sla_due_at.isoformat()
            if package.sla_due_at
            else None
        ),
    }


@router.post("/cases/{case_id}/package")
async def upload_self_filing_package(
    case_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor, case = await _lawyer_case(
        request,
        db,
        x_admin_token,
        case_id=case_id,
    )
    if str(case.status) != CaseStatus.M1_SELF_FILING_PREPARATION.value:
        raise HTTPException(
            status_code=409,
            detail="Итоговый пакет можно утвердить только на этапе подготовки",
        )
    package = await SelfFilingService(db).require_package(
        case_id=case.id,
        for_update=True,
    )
    try:
        expected_version = int(request.headers.get("x-package-version") or "")
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail="Не передана актуальная версия карточки пакета",
        ) from error
    if expected_version != int(package.version or 0):
        raise HTTPException(
            status_code=409,
            detail="Карточка пакета уже изменилась. Обновите экран перед загрузкой.",
        )

    document_type = str(
        request.headers.get("x-deliverable-type") or ""
    ).strip().upper()
    if document_type not in SELF_FILING_DELIVERABLE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                "Выберите один из четырёх документов: претензия, исковое заявление, "
                "расчёт суммы иска или дорожная карта клиента"
            ),
        )

    original_name = str(request.headers.get("x-file-name") or "").strip()
    if not original_name:
        raise HTTPException(status_code=400, detail="Не передано имя итогового файла")
    mime_type = str(
        request.headers.get("x-file-type")
        or request.headers.get("content-type")
        or ""
    ).strip() or None
    try:
        declared_size = int(request.headers.get("content-length") or 0) or None
    except ValueError:
        declared_size = None

    stored = None
    try:
        stored = await save_staff_upload(
            chunks=request.stream(),
            case_id=int(case.id),
            original_name=original_name,
            mime_type=mime_type,
            declared_size=declared_size,
        )
        document = await publish_self_filing_package(
            db,
            actor=DocumentActor(
                account=actor.account,
                payload=actor.token_payload,
                role=ROLE_LAWYER,
                lawyer_id=int(actor.lawyer.id),
            ),
            case=case,
            stored=stored,
            document_type=document_type,
        )
        package = await SelfFilingService(db).mark_package_ready(
            case_id=int(case.id),
            lawyer_id=int(actor.lawyer.id),
            document_id=int(document.id),
            document_type=document_type,
        )
        await db.commit()
    except UploadSecurityError as error:
        await db.rollback()
        raise HTTPException(status_code=400, detail=error.user_message) from error
    except DuplicateDocumentError as error:
        await db.rollback()
        if stored is not None:
            LocalStorageService().discard_stored_file(
                stored.storage_path,
                expected_case_id=int(case.id),
            )
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (SelfFilingError, ValueError) as error:
        await db.rollback()
        if stored is not None:
            LocalStorageService().discard_stored_file(
                stored.storage_path,
                expected_case_id=int(case.id),
            )
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        if stored is not None:
            try:
                LocalStorageService().discard_stored_file(
                    stored.storage_path,
                    expected_case_id=int(case.id),
                )
            except Exception:
                pass
        raise

    return {
        "ok": True,
        "document_id": int(document.id),
        "document_type": document_type,
        "document_version": int(document.version or 1),
        "sha256": document.sha256,
        "package_version": int(package.version or 1),
        "email_delivery_status": package.email_delivery_status,
    }


@router.post("/cases/{case_id}/email/retry")
async def retry_self_filing_email(
    case_id: int,
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor, case = await _lawyer_case(
        request,
        db,
        x_admin_token,
        case_id=case_id,
    )
    service = SelfFilingService(db)
    package = await service.require_package(case_id=case_id, for_update=True)
    _expect_version(package, payload)
    if package.email_delivery_status not in {EMAIL_FAILED, EMAIL_QUEUED}:
        raise HTTPException(
            status_code=409,
            detail="Повторная email-доставка сейчас не требуется",
        )
    try:
        sent = await SelfFilingEmailSender(db).send_one(int(package.id))
        await db.commit()
    except Exception as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "ok": bool(sent),
        "case_id": int(case.id),
        "lawyer_id": int(actor.lawyer.id),
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }


SELF_FILING_HTML = r"""
<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Пакет для самостоятельной подачи</title>
<style>
:root{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--green:#14804a;--red:#b42318;--amber:#a15c00}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif}
header{background:#111827;color:#fff}.top{max-width:1100px;margin:auto;padding:16px 18px;display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}
h1{font-size:20px;margin:0}.links{display:flex;gap:8px;flex-wrap:wrap}.links a{color:#fff;text-decoration:none;border:1px solid #ffffff45;border-radius:9px;padding:8px 10px}
main{max-width:1100px;margin:auto;padding:16px}.grid{display:grid;grid-template-columns:1.1fr .9fr;gap:14px}.card{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:16px;margin-bottom:14px}
.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:800}.status{font-size:20px;font-weight:800;margin:5px 0}.muted{color:var(--muted);font-size:13px;line-height:1.5}.kv{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}.cell{background:#f8fafc;border-radius:10px;padding:9px;min-width:0}.cell b{display:block;font-size:12px;margin-bottom:4px}.cell span{font-size:13px;word-break:break-word}
label{display:block;font-size:12px;font-weight:750;margin:10px 0 5px}input,textarea,select{width:100%;border:1px solid #cfd4dc;border-radius:9px;padding:10px;font:inherit;background:#fff}textarea{min-height:80px;resize:vertical}
button,.button{border:0;border-radius:9px;background:var(--blue);color:#fff;padding:10px 12px;font-weight:750;cursor:pointer;text-decoration:none;display:inline-block}.secondary{background:#475467}.danger{background:var(--red)}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.notice{padding:10px;border-radius:10px;background:#eef2ff;margin:10px 0}.bad{color:var(--red)}.ok{color:var(--green)}.warn{color:var(--amber)}.doc{padding:9px 0;border-bottom:1px solid var(--line)}.doc:last-child{border-bottom:0}
@media(max-width:760px){.grid{grid-template-columns:1fr}.kv{grid-template-columns:1fr}.top{align-items:flex-start}.actions>*{flex:1;text-align:center}}
</style></head>
<body>
<header><div class="top"><div><h1>📄 Пакет для самостоятельной подачи</h1><div id="sub" class="muted" style="color:#d0d5dd"></div></div><div class="links"><a href="/lawyer/workspace/ui">Кабинет юриста</a><a href="/admin/workdesk/ui">Дела клиентов</a><a href="/self-filing/readiness">Готовность услуги</a></div></div></header>
<main><div id="feedback"></div><div class="grid"><section>
<div class="card"><div class="eyebrow">Сейчас</div><div id="status" class="status">Загрузка…</div><div id="now" class="muted"></div><div id="facts" class="kv"></div></div>
<div class="card"><div class="eyebrow">Документы</div><div id="docs"></div><div class="actions"><a id="materials" class="button secondary" href="#">Открыть защищённые материалы</a><a id="messages" class="button secondary" href="#">Переписка</a></div></div>
<div class="card" id="uploadCard"><div class="eyebrow">Судебный комплект: ровно 4 документа</div><div class="muted">Загружайте финальные версии по отдельности: претензия, исковое заявление, расчёт суммы иска и дорожная карта клиента. Email-доставка откроется только когда утверждены все четыре.</div><label>Тип документа</label><select id="deliverableType"><option value="SELF_FILING_PRETRIAL_CLAIM">Претензия</option><option value="SELF_FILING_STATEMENT_OF_CLAIM">Исковое заявление</option><option value="SELF_FILING_CLAIM_CALCULATION">Расчёт суммы иска</option><option value="SELF_FILING_CLIENT_ROADMAP">Дорожная карта клиента</option></select><input id="file" type="file"><div class="actions"><button class="lawyer-action" onclick="uploadPackage()">Утвердить документ</button></div></div>
</section><aside>
<div class="card"><div class="eyebrow">Действие юриста</div><div class="actions"><button id="startReviewButton" class="lawyer-action" onclick="startReview()">Начать проверку</button></div>
<label>Что нужно дополнить</label><textarea id="reason" placeholder="Конкретно укажите отсутствующий документ или исправление"></textarea><button id="requestDocsButton" class="lawyer-action" onclick="requestDocs()">Запросить документы</button>
<hr style="border:0;border-top:1px solid var(--line);margin:16px 0">
<label>Суд</label><input id="court" placeholder="Полное наименование суда">
<label>Адрес суда</label><textarea id="courtAddress"></textarea>
<label>Основание подсудности</label><select id="basis"></select>
<label>Юридическое обоснование</label><textarea id="note" placeholder="Почему выбран именно этот суд и на каком основании"></textarea>
<label style="display:flex;gap:9px;align-items:flex-start;font-weight:600"><input id="completeConfirm" type="checkbox" style="width:auto;margin-top:3px"> <span>Подтверждаю как ответственный юрист: проверены ДДУ, паспорт/иной документ личности, все имеющиеся приложения и дополнительные соглашения к ДДУ, а также иные материалы, необходимые для подготовки полного пакета. Автоматически определить отсутствие не загруженного приложения система не может.</span></label>
<hr style="border:0;border-top:1px solid var(--line);margin:16px 0">
<label style="display:flex;gap:9px;align-items:flex-start;font-weight:600"><input id="actSigned" type="checkbox" style="width:auto;margin-top:3px" onchange="toggleActDate()"> <span>Акт передачи квартиры подписан.</span></label>
<label>Дата подписания акта</label><input id="actDate" type="date" disabled>
<div class="muted">Если акт не подписан, расчёт для судебного комплекта будет привязан к дате фактической оплаты услуги. Если акт подписан — к дате акта.</div>
<div class="actions"><button id="approveButton" class="lawyer-action" onclick="approve()">Подтвердить комплект и открыть 15 000 ₽</button></div>
</div>
<div class="card"><div class="eyebrow">Оплата клиента</div><div id="bankPayment" class="muted">Загрузка…</div></div>
<div class="card" id="paymentReviewCard"><div class="eyebrow">Финансовая сверка</div><div id="paymentReview" class="muted"></div><label>Комментарий администратора</label><textarea id="financialComment" placeholder="Причина возобновления либо возврата, минимум 10 символов"></textarea><div class="actions"><button class="finance-action" onclick="resolvePayment('resume')">Запустить подготовку по полученным деньгам</button><button class="danger finance-action" onclick="resolvePayment('refund_pending')">Направить на контролируемый возврат</button></div></div>
<div class="card"><div class="eyebrow">Доставка</div><div id="delivery" class="muted"></div><div class="actions"><button id="retryEmailButton" class="secondary lawyer-action" onclick="retryEmail()">Повторить email-доставку</button><button class="secondary" onclick="load()">Обновить</button></div></div>
</aside></div></main>
<script>
const qs=new URLSearchParams(location.search);const caseId=Number(qs.get('case_id'));let data=null;
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
function dt(s){
 if(!s)return '—';
 try{
   const zone=(data&&data.capabilities&&data.capabilities.business_timezone)||'Europe/Moscow';
   const label=(data&&data.capabilities&&data.capabilities.business_timezone_label)||'';
   const shown=new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short',timeZone:zone}).format(new Date(s));
   return label?shown+' '+label:shown;
 }catch(_){return String(s)}
}
function feedback(text,bad=false){document.getElementById('feedback').innerHTML='<div class="card '+(bad?'bad':'ok')+'">'+esc(text)+'</div>'}
async function api(path,opts={}){const r=await fetch(path,{credentials:'same-origin',...opts});const t=await r.text();let j={};try{j=JSON.parse(t)}catch{}if(!r.ok)throw new Error(j.detail||t||('HTTP '+r.status));return j}
function payload(extra={}){return {expected_package_version:Number(data.package.version),...extra}}
async function post(path,body){const out=await api(path,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});feedback('Сохранено');await load();return out}
async function load(){
 if(!caseId){feedback('Не указан case_id',true);return}
 try{data=await api('/self-filing/cases/'+caseId);render()}catch(e){feedback(e.message,true)}
}
function render(){
 const c=data.case,p=data.package,a=data.actor;
 document.getElementById('sub').textContent='Обращение '+c.number+' · '+data.client.name;
 document.getElementById('status').textContent=c.status_label||'Статус требует уточнения';
 const roleNote=a.can_mutate?'Вы отвечаете за это обращение. Все решения ниже привязаны к текущей версии карточки.':'Режим просмотра: юридические решения доступны только ответственному юристу.';
 document.getElementById('now').innerHTML='<b>Главное следующее действие:</b> '+esc(c.next_action||'Уточнить статус')+'<br>'+esc(roleNote);
 document.getElementById('facts').innerHTML=[
  ['Регион',p.region],
  ['Email',p.delivery_email],
  ['Email подтверждён',p.email_verified?('Да · '+dt(p.email_confirmed_at)):(p.email_verification_pending?'Ожидается код до '+dt(p.email_verification_expires_at):'Нет')],
  ['Полный комплект',dt(p.documents_complete_at)],['Суд',p.court_name],
  ['Акт передачи подписан',p.transfer_act_signed===true?'Да':(p.transfer_act_signed===false?'Нет':'Не подтверждено')],
  ['Дата акта',p.transfer_act_date],
  ['Оплата подтверждена',dt(p.payment_confirmed_at)],
  ['Расчёт суммы иска на дату',p.claim_calculation_cutoff_date],
  ['Основание даты',p.claim_calculation_basis],
  ['Неустойка в расчётном снимке',p.claim_calculation_amount],
  ['Нужно уточнение в суде',p.claim_update_in_court_required===true?'Да':(p.claim_update_in_court_required===false?'Нет':'—')],
  ['Выдать до',dt(p.sla_due_at)],['Готово',dt(p.ready_at)],['Доставлено',dt(p.delivered_at)]
 ].map(([k,v])=>'<div class="cell"><b>'+esc(k)+'</b><span>'+esc(v||'—')+'</span></div>').join('');
 document.getElementById('docs').innerHTML=data.documents.length?data.documents.map(d=>'<div class="doc"><b>'+esc(d.title)+' · v'+d.version+'</b><div class="muted">'+esc(d.status)+' · SHA '+esc(d.sha256_prefix||'—')+(d.lawyer_comment?'<br>'+esc(d.lawyer_comment):'')+'</div></div>').join(''):'<div class="muted">Документов нет.</div>';
 document.getElementById('materials').href='/document-access/ui?case_id='+caseId;
 document.getElementById('messages').href='/message-center/ui?case_id='+caseId;
 const basis=document.getElementById('basis');basis.innerHTML=data.capabilities.jurisdiction_bases.map(x=>'<option value="'+esc(x)+'">'+esc(x)+'</option>').join('');
 if(p.court_name)document.getElementById('court').value=p.court_name;
 if(p.court_address)document.getElementById('courtAddress').value=p.court_address;
 if(p.jurisdiction_basis)basis.value=p.jurisdiction_basis;
 if(p.jurisdiction_note)document.getElementById('note').value=p.jurisdiction_note;
 document.getElementById('actSigned').checked=p.transfer_act_signed===true;
 if(p.transfer_act_date)document.getElementById('actDate').value=p.transfer_act_date;
 const bankPayment=[...data.payments].reverse().find(x=>x.code==='M1_SELF_FILING_PACKAGE');
 const bank=data.capabilities.bank_requisites||{};
 document.getElementById('bankPayment').innerHTML=
   '<b>Только банковский перевод по реквизитам коллегии адвокатов.</b><br>'+
   'Получатель: '+esc(bank.recipient||'—')+'<br>'+
   'ИНН: '+esc(bank.inn||'—')+' · КПП: '+esc(bank.kpp||'—')+'<br>'+
   'ОГРН: '+esc(bank.ogrn||'—')+'<br>'+
   'р/с: '+esc(bank.settlement_account||'—')+'<br>'+
   'к/с: '+esc(bank.correspondent_account||'—')+'<br>'+
   'Банк: '+esc(bank.bank||'—')+'<br>'+
   'БИК: '+esc(bank.bik||'—')+'<br><br>'+
   '<b>Обязательная пометка:</b> '+esc((bankPayment&&bankPayment.payment_purpose)||bank.mandatory_purpose||'—')+
   (bankPayment?'<br><br>Платёж #'+bankPayment.id+' · '+esc(bankPayment.amount)+' RUB · '+esc(bankPayment.status):'');
 document.getElementById('delivery').innerHTML='Адрес подтверждён: <b>'+(p.email_verified?'да':'нет')+'</b>'+(p.email_verification_pending?'<br>Код действует до: '+esc(dt(p.email_verification_expires_at))+'<br>Ошибочных попыток: '+p.email_verification_attempts:'')+'<br><br>Доставка пакета: <b>'+esc(p.email_delivery_status)+'</b><br>Попыток доставки: '+p.email_delivery_attempts+'<br>Message-ID: '+esc(p.email_message_id||'—')+'<br>Последняя ошибка: '+esc(p.email_last_error||'—')+'<br>Email provider: '+(data.capabilities.email_delivery_configured?'готов':'НЕ НАСТРОЕН')+(data.capabilities.email_delivery_configuration_error?'<br><span class="bad">'+esc(data.capabilities.email_delivery_configuration_error)+'</span>':'');
 const reviewPayment=[...data.payments].reverse().find(x=>x.code==='M1_SELF_FILING_PACKAGE'&&['PAID_REVIEW','REFUND_PENDING','REFUND_DECLINED'].includes(String(x.status)));
 const reviewCard=document.getElementById('paymentReviewCard');
 if(reviewPayment){
   reviewCard.style.display='block';
   document.getElementById('paymentReview').innerHTML='Платёж #'+reviewPayment.id+' · '+esc(reviewPayment.amount)+' RUB · <b>'+esc(reviewPayment.status)+'</b><br>'+(reviewPayment.status==='PAID_REVIEW'?'Деньги получены, но SLA не запущен. Повторно брать оплату нельзя: администратор должен либо восстановить запуск по исходному времени поступления, либо направить деньги на возврат.':'Повторная оплата заблокирована до завершения финансовой сверки/возврата.');
   reviewCard.dataset.paymentId=String(reviewPayment.id);
 }else{
   reviewCard.style.display='none';
   reviewCard.dataset.paymentId='';
 }
 const status=String(c.status||'');
 const canStartReview=Boolean(a.can_mutate&&['M1_SELF_FILING_DOCUMENTS_RECEIVED','M1_SELF_FILING_DOCS_REQUESTED'].includes(status));
 const canReviewDecision=Boolean(a.can_mutate&&['M1_SELF_FILING_DOCUMENTS_RECEIVED','M1_SELF_FILING_LAWYER_REVIEW'].includes(status));
 const canFinance=Boolean(a.can_financial_reconcile&&reviewPayment&&reviewPayment.status==='PAID_REVIEW');
 document.getElementById('startReviewButton').disabled=!canStartReview;
 document.getElementById('requestDocsButton').disabled=!canReviewDecision;
 document.getElementById('approveButton').disabled=!canReviewDecision;
 ['reason','court','courtAddress','basis','note','completeConfirm','actSigned'].forEach(id=>{document.getElementById(id).disabled=!canReviewDecision});
 document.querySelectorAll('.finance-action').forEach(el=>{el.disabled=!canFinance});
 document.getElementById('financialComment').disabled=!canFinance;
 document.getElementById('retryEmailButton').disabled=!(a.can_mutate&&['FAILED','QUEUED'].includes(String(p.email_delivery_status||'')));
 document.getElementById('file').disabled=!(status==='M1_SELF_FILING_PREPARATION'&&a.can_mutate);
 document.getElementById('deliverableType').disabled=!(status==='M1_SELF_FILING_PREPARATION'&&a.can_mutate);
 document.getElementById('uploadCard').style.display=(status==='M1_SELF_FILING_PREPARATION'&&a.can_mutate)?'block':'none';
 toggleActDate();
}
function toggleActDate(){const checkbox=document.getElementById('actSigned'),signed=checkbox.checked,dateInput=document.getElementById('actDate');dateInput.disabled=checkbox.disabled||!signed;if(!signed)dateInput.value=''}
async function startReview(){try{await post('/self-filing/cases/'+caseId+'/review/start',payload())}catch(e){feedback(e.message,true)}}
async function requestDocs(){try{await post('/self-filing/cases/'+caseId+'/request-documents',payload({reason:document.getElementById('reason').value}))}catch(e){feedback(e.message,true)}}
async function approve(){const confirmed=document.getElementById('completeConfirm').checked,signed=document.getElementById('actSigned').checked,actDate=document.getElementById('actDate').value;if(!confirmed){feedback('Сначала явно подтвердите полноту комплекта документов.',true);return}if(signed&&!actDate){feedback('Укажите дату подписания акта передачи.',true);return}try{await post('/self-filing/cases/'+caseId+'/approve-for-payment',payload({court_name:document.getElementById('court').value,court_address:document.getElementById('courtAddress').value,jurisdiction_basis:document.getElementById('basis').value,jurisdiction_note:document.getElementById('note').value,completeness_confirmed:confirmed,transfer_act_signed:signed,transfer_act_date:signed?actDate:null}))}catch(e){feedback(e.message,true)}}
async function resolvePayment(decision){const card=document.getElementById('paymentReviewCard'),paymentId=Number(card.dataset.paymentId||0),comment=document.getElementById('financialComment').value;if(!paymentId){feedback('Платёж для сверки не найден',true);return}try{await post('/self-filing/cases/'+caseId+'/payment-review/'+paymentId+'/resolve',payload({decision,comment}));document.getElementById('financialComment').value=''}catch(e){feedback(e.message,true)}}
async function uploadPackage(){const f=document.getElementById('file').files[0],dtype=document.getElementById('deliverableType').value;if(!f){feedback('Выберите файл',true);return}try{const out=await api('/self-filing/cases/'+caseId+'/package',{method:'POST',headers:{'x-file-name':f.name,'x-file-type':f.type||'application/octet-stream','x-deliverable-type':dtype,'x-package-version':String(data.package.version)},body:f});feedback('Документ утверждён. SHA '+String(out.sha256||'').slice(0,12));document.getElementById('file').value='';await load()}catch(e){feedback(e.message,true)}}
async function retryEmail(){try{await post('/self-filing/cases/'+caseId+'/email/retry',payload())}catch(e){feedback(e.message,true)}}
load();
</script></body></html>
"""


@router.get("/ui", response_class=HTMLResponse)
async def self_filing_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    token = _token(request, x_admin_token)
    if not token:
        return RedirectResponse(url="/login", status_code=303)
    try:
        await resolve_document_actor(db, token)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(
        SELF_FILING_HTML,
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router"]
