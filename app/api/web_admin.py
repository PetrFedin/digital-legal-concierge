from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.case_detail_page import CASE_DETAIL_HTML
from app.api.admin import (
    payment_can_be_confirmed_offline,
    payment_can_be_manually_confirmed,
    require_admin,
)
from app.config import settings
from app.db.session import get_db
from app.domain.cases.case_activity import CaseActivityService
from app.domain.cases.admin_manual_status_policy import (
    M1_FINANCIAL_MANAGED_STATUSES,
    manual_status_change_allowed,
)
from app.domain.cases.case_timeline import get_client_visible_status
from app.domain.cases.m1_financial_summary import M1FinancialSummaryService
from app.domain.cases.service_modes import M1ServiceMode
from app.domain.documents.document_workflow import ACTIONABLE_REVIEW_STATUSES
from app.domain.statuses.case_statuses import CaseStatus
from app.models.calculation import Calculation
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.lawyer import Lawyer
from app.models.message import Message
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.self_filing_package import SelfFilingPackage
from app.models.user import User

router = APIRouter(tags=["web-admin"])

CLOSED_STATUSES = {"M1_CLOSED", "M1_SELF_FILING_CLOSED", "M2_CLOSED", "ARCHIVED"}
DOCUMENT_REVIEW_STATUSES = ACTIONABLE_REVIEW_STATUSES
QUEUE_NAMES = {"unassigned", "documents", "consultations", "overdue"}

ROUTE_LABELS = {
    "M1": "Ведение дела",
    "M2": "Консультация",
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
DOCUMENT_STATUS_LABELS = {
    "UPLOADED": "Ожидает передачи юристу",
    "ON_REVIEW": "На проверке у юриста",
    "PENDING": "Ожидает уточнения статуса",
    "PENDING_REVIEW": "Ожидает уточнения статуса",
    "REVIEW_PENDING": "Ожидает уточнения статуса",
    "REVIEW_REQUIRED": "Требуется уточнение статуса",
    "NEEDS_REVIEW": "Требуется уточнение статуса",
    "APPROVED": "Принят",
    "ACCEPTED": "Принят",
    "VERIFIED": "Принят",
    "REJECTED": "Отклонён",
    "NEEDS_REUPLOAD": "Нужно загрузить заново",
}


def _route_label(route: str | None) -> str:
    return ROUTE_LABELS.get(str(route or ""), "Юридическое обращение")


def _case_route_label(case: Case) -> str:
    if (
        str(case.route or "") == "M1"
        and str(case.service_mode or "") == M1ServiceMode.SELF_FILING_PACKAGE.value
    ):
        return "Пакет для самостоятельной подачи"
    return _route_label(case.route)


def _sla_label(status: str | None) -> str:
    return SLA_LABELS.get(str(status or ""), "SLA не определён")


def _document_status_label(status: str | None) -> str:
    return DOCUMENT_STATUS_LABELS.get(str(status or ""), "Загружен")


def _recommended_action(case: Case) -> str:
    if case.assigned_lawyer_id is None:
        return "Назначить ответственного юриста"
    if str(case.sla_status or "") in {"FIRST_RESPONSE_OVERDUE", "ACTION_OVERDUE"}:
        return "Устранить просрочку и зафиксировать результат"
    return case.next_action or "Проверить карточку и определить следующий этап"


def _manual_status_options(case: Case) -> list[str]:
    return [
        status.value
        for status in CaseStatus
        if status.value != str(case.status)
        and manual_status_change_allowed(case.status, status.value)
    ]


def _manual_status_note(case: Case) -> str | None:
    if case.status in M1_FINANCIAL_MANAGED_STATUSES:
        return (
            "Финальный финансовый этап защищён. Фактическое взыскание фиксирует "
            "назначенный юрист, а подтверждение success fee и закрытие выполняет "
            "платёжный webhook. Ручная смена статуса здесь недоступна."
        )
    return None


def _case_row(
    case: Case,
    *,
    queue: str,
    lawyer_name: str | None = None,
) -> dict[str, object]:
    return {
        "id": case.id,
        "number": case.case_number,
        "route": case.route,
        "route_label": _case_route_label(case),
        "status": case.status,
        "status_label": get_client_visible_status(case.status),
        "lawyer_id": case.assigned_lawyer_id,
        "lawyer_name": lawyer_name,
        "next_action": _recommended_action(case),
        "sla_status": case.sla_status,
        "sla_label": _sla_label(case.sla_status),
        "sla_due_at": case.sla_due_at.isoformat() if case.sla_due_at else None,
        "created_at": case.created_at.isoformat(),
        "queue": queue,
    }


@router.get("/admin/work-queues/{queue_name}")
async def work_queue(
    queue_name: str,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    if queue_name not in QUEUE_NAMES:
        raise HTTPException(status_code=404, detail="Неизвестная рабочая очередь")

    stmt = select(Case).where(Case.status.notin_(CLOSED_STATUSES))
    if queue_name == "unassigned":
        stmt = stmt.where(Case.assigned_lawyer_id.is_(None)).order_by(
            Case.created_at.asc()
        )
    elif queue_name == "documents":
        stmt = (
            stmt.join(Document, Document.case_id == Case.id)
            .where(Document.status.in_(DOCUMENT_REVIEW_STATUSES))
            .order_by(Case.created_at.asc())
        )
    elif queue_name == "consultations":
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start.replace(
            hour=23,
            minute=59,
            second=59,
            microsecond=999999,
        )
        stmt = (
            stmt.join(Consultation, Consultation.case_id == Case.id)
            .where(
                (Consultation.status == "BOOKED")
                | (Consultation.status == "CONFIRMED")
            )
            .where(Consultation.scheduled_at >= day_start)
            .where(Consultation.scheduled_at <= day_end)
            .order_by(Consultation.scheduled_at.asc())
        )
    else:
        stmt = stmt.where(
            Case.sla_status.in_(["FIRST_RESPONSE_OVERDUE", "ACTION_OVERDUE"])
        ).order_by(Case.sla_due_at.asc())

    result = await db.execute(stmt.limit(200))
    cases = list(result.scalars().unique().all())
    lawyer_ids = {case.assigned_lawyer_id for case in cases if case.assigned_lawyer_id}
    lawyer_names: dict[int, str] = {}
    if lawyer_ids:
        lawyers = (
            await db.execute(select(Lawyer).where(Lawyer.id.in_(lawyer_ids)))
        ).scalars().all()
        lawyer_names = {lawyer.id: lawyer.full_name for lawyer in lawyers}

    return {
        "queue": queue_name,
        "count": len(cases),
        "items": [
            _case_row(
                case,
                queue=queue_name,
                lawyer_name=lawyer_names.get(case.assigned_lawyer_id),
            )
            for case in cases
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/admin/case-workspace/{case_id}")
async def case_workspace(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    case = await db.get(Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Дело не найдено")

    client = await db.get(User, case.client_id)
    lawyer = (
        await db.get(Lawyer, case.assigned_lawyer_id)
        if case.assigned_lawyer_id
        else None
    )
    documents = (
        await db.execute(
            select(Document)
            .where(Document.case_id == case.id)
            .order_by(Document.created_at.desc())
        )
    ).scalars().all()
    payments = (
        await db.execute(
            select(Payment)
            .where(Payment.case_id == case.id)
            .order_by(Payment.created_at.desc())
        )
    ).scalars().all()
    financial_final = await M1FinancialSummaryService(db).build(case)
    manual_status_options = _manual_status_options(case)
    calculation = (
        await db.execute(
            select(Calculation)
            .where(Calculation.case_id == case.id)
            .order_by(Calculation.created_at.desc(), Calculation.id.desc())
            .limit(1)
        )
    ).scalars().first()
    self_filing = None
    if str(case.service_mode or "") == M1ServiceMode.SELF_FILING_PACKAGE.value:
        self_filing = (
            await db.execute(
                select(SelfFilingPackage).where(
                    SelfFilingPackage.case_id == int(case.id)
                )
            )
        ).scalar_one_or_none()
    message_count = int(
        await db.scalar(
            select(func.count(Message.id)).where(Message.case_id == case.id)
        )
        or 0
    )
    unread_client_messages = int(
        await db.scalar(
            select(func.count(Message.id)).where(
                Message.case_id == case.id,
                Message.sender_type == "client",
                Message.is_read.is_(False),
            )
        )
        or 0
    )
    recent_messages = list(
        (
            await db.execute(
                select(Message)
                .where(Message.case_id == case.id)
                .order_by(Message.id.desc())
                .limit(8)
            )
        ).scalars().all()
    )
    notification_count = int(
        await db.scalar(
            select(func.count(Notification.id)).where(Notification.case_id == case.id)
        )
        or 0
    )
    recent_notifications = list(
        (
            await db.execute(
                select(Notification)
                .where(Notification.case_id == case.id)
                .order_by(Notification.id.desc())
                .limit(8)
            )
        ).scalars().all()
    )
    activity = await CaseActivityService(db).page(
        case_id=int(case.id),
        audience="staff",
        limit=10,
    )

    return {
        "case": {
            "id": case.id,
            "number": case.case_number,
            "route": case.route,
            "service_mode": case.service_mode,
            "route_label": _case_route_label(case),
            "status": case.status,
            "status_label": get_client_visible_status(case.status),
            "next_action": _recommended_action(case),
            "lawyer_id": case.assigned_lawyer_id,
            "lawyer_name": lawyer.full_name if lawyer else None,
            "sla_status": case.sla_status,
            "sla_label": _sla_label(case.sla_status),
            "sla_due_at": case.sla_due_at.isoformat() if case.sla_due_at else None,
            "created_at": case.created_at.isoformat() if case.created_at else None,
            "updated_at": case.updated_at.isoformat(),
            "manual_status_options": manual_status_options,
            "manual_status_locked": not bool(manual_status_options),
            "manual_status_note": _manual_status_note(case),
        },
        "client": (
            {
                "id": client.id,
                "name": client.full_name,
                "phone": client.phone,
                "email": client.email,
                "telegram_id": client.telegram_id,
                "username": client.telegram_username,
                "profile_url": f"/search-center/ui?q={client.telegram_id}",
            }
            if client
            else None
        ),
        "documents": [
            {
                "id": document.id,
                "type": document.document_type,
                "title": document.title,
                "file_name": document.file_name,
                "status": document.status,
                "status_label": _document_status_label(document.status),
                "lawyer_comment": document.lawyer_comment,
                "version": document.version,
            }
            for document in documents
        ],
        "payments": [
            {
                "id": payment.id,
                "case_id": payment.case_id,
                "payment_code": payment.payment_code,
                "title": payment.title,
                "amount": float(payment.amount),
                "currency": payment.currency,
                "status": payment.status,
                "provider": payment.provider,
                "created_at": (
                    payment.created_at.isoformat() if payment.created_at else None
                ),
                "updated_at": (
                    payment.updated_at.isoformat() if payment.updated_at else None
                ),
                "manual_confirm_allowed": payment_can_be_manually_confirmed(payment),
                "offline_confirm_allowed": payment_can_be_confirmed_offline(payment),
            }
            for payment in payments
        ],
        "calculation": (
            {
                "id": calculation.id,
                "contract_price": float(calculation.contract_price)
                if calculation.contract_price is not None
                else None,
                "planned_transfer_date": (
                    calculation.planned_transfer_date.isoformat()
                    if calculation.planned_transfer_date
                    else None
                ),
                "actual_transfer_date": (
                    calculation.actual_transfer_date.isoformat()
                    if calculation.actual_transfer_date
                    else None
                ),
                "calculation_date": (
                    calculation.calculation_date.isoformat()
                    if calculation.calculation_date
                    else None
                ),
                "object_transferred": calculation.object_transferred,
                "delay_days": calculation.delay_days_chargeable
                if calculation.delay_days_chargeable is not None
                else calculation.delay_days,
                "penalty_amount": float(calculation.penalty_amount)
                if calculation.penalty_amount is not None
                else None,
                "is_preliminary": bool(calculation.is_preliminary),
                "manual_review_required": calculation.manual_review_required,
            }
            if calculation
            else None
        ),
        "self_filing": (
            {
                "status": self_filing.status,
                "version": int(self_filing.version or 1),
                "delivery_email": self_filing.delivery_email,
                "email_confirmed_at": (
                    self_filing.email_confirmed_at.isoformat()
                    if self_filing.email_confirmed_at
                    else None
                ),
                "documents_complete_at": (
                    self_filing.documents_complete_at.isoformat()
                    if self_filing.documents_complete_at
                    else None
                ),
                "court_name": self_filing.court_name,
                "court_address": self_filing.court_address,
                "jurisdiction_basis": self_filing.jurisdiction_basis,
                "payment_confirmed_at": (
                    self_filing.payment_confirmed_at.isoformat()
                    if self_filing.payment_confirmed_at
                    else None
                ),
                "sla_started_at": (
                    self_filing.sla_started_at.isoformat()
                    if self_filing.sla_started_at
                    else None
                ),
                "sla_due_at": (
                    self_filing.sla_due_at.isoformat()
                    if self_filing.sla_due_at
                    else None
                ),
                "ready_at": (
                    self_filing.ready_at.isoformat()
                    if self_filing.ready_at
                    else None
                ),
                "delivered_at": (
                    self_filing.delivered_at.isoformat()
                    if self_filing.delivered_at
                    else None
                ),
                "email_delivery_status": self_filing.email_delivery_status,
                "email_delivery_attempts": int(
                    self_filing.email_delivery_attempts or 0
                ),
                "product_url": f"/self-filing/ui?case_id={int(case.id)}",
            }
            if self_filing
            else None
        ),
        "communications": {
            "message_count": message_count,
            "unread_client_messages": unread_client_messages,
            "notification_count": notification_count,
            "recent_messages": [
                {
                    "id": message.id,
                    "sender_type": message.sender_type,
                    "text": message.text,
                    "is_read": bool(message.is_read),
                    "created_at": (
                        message.created_at.isoformat() if message.created_at else None
                    ),
                }
                for message in recent_messages
            ],
            "recent_notifications": [
                {
                    "id": notification.id,
                    "event_code": notification.event_code,
                    "title": notification.title,
                    "text": notification.text,
                    "status": notification.status,
                    "sent_at": (
                        notification.sent_at.isoformat()
                        if notification.sent_at
                        else None
                    ),
                    "created_at": (
                        notification.created_at.isoformat()
                        if notification.created_at
                        else None
                    ),
                }
                for notification in recent_notifications
            ],
        },
        "activity": activity,
        "business_timezone": settings.business_timezone,
        "business_timezone_label": settings.business_timezone_label,
        "financial_final": financial_final,
    }


@router.get("/admin/cases/{case_id}/ui", response_class=HTMLResponse)
async def admin_case_detail_ui(
    case_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Authenticated detailed Case card used by all admin payment links."""

    token = x_admin_token or request.cookies.get(settings.admin_session_cookie)
    if not token:
        return RedirectResponse(
            url=f"/login?next=/admin/cases/{int(case_id)}/ui",
            status_code=303,
        )
    require_admin(token)
    case = await db.get(Case, int(case_id))
    if case is None:
        raise HTTPException(status_code=404, detail="Дело не найдено")
    return HTMLResponse(
        CASE_DETAIL_HTML.replace("__CASE_ID__", str(int(case_id))),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/admin-ui", response_class=HTMLResponse)
async def admin_ui():
    return HTMLResponse(ADMIN_HTML)


ADMIN_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Рабочий кабинет администратора</title>
<style>
:root{--bg:#f4f6fa;--surface:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--primary:#3157d5;--primary-soft:#eef2ff;--green:#14804a;--green-soft:#ecfdf3;--red:#b42318;--red-soft:#fef3f2;--amber:#a15c00;--amber-soft:#fff7e6;--shadow:0 10px 30px rgba(16,24,40,.07)}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}header{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:18px 24px;display:flex;justify-content:space-between;align-items:center;gap:16px}header h1{font-size:20px;margin:0 0 4px}header p{margin:0;color:#d0d5dd;font-size:13px}.layout{display:grid;grid-template-columns:230px minmax(0,1fr) 370px;min-height:calc(100vh - 76px)}nav,.side{background:var(--surface);padding:18px}.side{border-left:1px solid var(--line);overflow:auto}nav{border-right:1px solid var(--line)}.content{padding:22px;overflow:auto}.nav-title{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:14px 10px 6px}.nav-button{display:block;width:100%;text-align:left;border:0;background:transparent;color:var(--ink);border-radius:10px;padding:10px 11px;cursor:pointer;font-weight:650}.nav-button:hover,.nav-button.active{background:var(--primary-soft);color:#2445b5}.header-actions,.toolbar,.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.toolbar{justify-content:space-between;margin-bottom:14px}.card{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:16px;box-shadow:var(--shadow)}.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.metric{border:1px solid var(--line);background:#fff;border-radius:14px;padding:15px;cursor:pointer;text-align:left}.metric:hover{border-color:#b8c4f4;background:var(--primary-soft)}.metric b{display:block;font-size:27px;margin-bottom:4px}.metric span{font-size:13px;color:var(--muted)}.metric.urgent{background:var(--red-soft);border-color:#fecdca}.metric.warn{background:var(--amber-soft);border-color:#fedf89}.section-title{display:flex;justify-content:space-between;align-items:end;gap:12px;margin:22px 0 10px}.section-title h2{margin:0;font-size:20px}.section-title p{margin:0;color:var(--muted);font-size:13px}.queue-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.queue-card{border:1px solid var(--line);background:#fff;border-radius:14px;padding:15px;cursor:pointer;text-align:left}.queue-card:hover{border-color:#b8c4f4;transform:translateY(-1px)}.queue-card b{font-size:17px}.queue-card .count{float:right;font-size:22px;font-weight:800}.list-item{border:1px solid var(--line);border-radius:13px;padding:13px;margin:9px 0;background:#fff}.list-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.badge{display:inline-flex;padding:4px 8px;border-radius:999px;background:#eef2f6;font-size:12px;font-weight:700}.badge.red{color:var(--red);background:var(--red-soft)}.badge.amber{color:var(--amber);background:var(--amber-soft)}.muted{font-size:13px;color:var(--muted)}.ok{color:var(--green)}.bad{color:var(--red)}.warn-text{color:var(--amber)}.action-box{background:var(--primary-soft);border:1px solid #c7d2fe;border-radius:13px;padding:13px;margin:12px 0}.action-box b{display:block;margin-bottom:5px}.data-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}.data-cell{background:#f8fafc;border-radius:10px;padding:10px}.data-cell span{display:block;color:var(--muted);font-size:12px;margin-bottom:3px}button,.button{border:0;border-radius:9px;padding:9px 12px;background:var(--primary);color:#fff;font-weight:700;cursor:pointer;text-decoration:none;display:inline-block}button.secondary,.button.secondary{background:#475467}button.green{background:var(--green)}button:disabled,input:disabled,select:disabled,textarea:disabled{opacity:.55;cursor:wait}.empty,.error,.loading{padding:28px;text-align:center;border:1px dashed var(--line);border-radius:14px;color:var(--muted)}.error{color:var(--red);background:var(--red-soft)}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{font-size:12px;color:var(--muted);text-transform:uppercase}input,select,textarea{width:100%;padding:9px;border:1px solid #d0d5dd;border-radius:9px;margin:5px 0}textarea{min-height:90px;resize:vertical}.raw-toggle{font-size:12px;color:var(--muted);cursor:pointer}pre{white-space:pre-wrap;word-break:break-word;background:#111827;color:#e5e7eb;padding:12px;border-radius:10px;max-height:240px;overflow:auto}.side h3{margin-top:0}.side-section{border-top:1px solid var(--line);padding-top:14px;margin-top:14px}@media(max-width:1120px){.layout{grid-template-columns:210px 1fr}.side{grid-column:1/-1;border-left:0;border-top:1px solid var(--line)}}@media(max-width:760px){header{align-items:flex-start;flex-direction:column}.layout{display:block}nav{display:flex;gap:6px;overflow:auto;padding:10px;border-right:0}.nav-title{display:none}.nav-button{white-space:nowrap;width:auto}.content{padding:14px}.metric-grid,.queue-grid{grid-template-columns:1fr 1fr}.side{padding:14px}}@media(max-width:500px){.metric-grid,.queue-grid,.data-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<header><div><h1>⚖ Рабочий кабинет администратора</h1><p>Операционный кабинет администратора</p></div><div class="header-actions"><a class="button secondary" href="/message-center/ui">Сообщения</a><a class="button secondary" href="/admin/sla/ui">SLA</a><a class="button secondary" href="/operator">Все разделы</a><form method="post" action="/logout" style="margin:0"><button class="secondary" type="submit">Выйти</button></form></div></header>
<div class="layout">
<nav><div class="nav-title">Работа</div><button class="nav-button active" data-tab="dashboard" onclick="showTab('dashboard',this)">Обзор</button><button class="nav-button" data-tab="queue" onclick="showTab('queue',this)">Рабочие очереди</button><button class="nav-button" data-tab="cases" onclick="showTab('cases',this)">Все дела</button><button class="nav-button" data-tab="documents" onclick="showTab('documents',this)">Документы</button><div class="nav-title">Команда</div><button class="nav-button" data-tab="lawyers" onclick="showTab('lawyers',this)">Юристы</button><button class="nav-button" data-tab="notifications" onclick="showTab('notifications',this)">Уведомления</button><div class="nav-title">Система</div><button class="nav-button" data-tab="payments" onclick="showTab('payments',this)">Платежи</button><button class="nav-button" data-tab="settings" onclick="showTab('settings',this)">Настройки</button></nav>
<main class="content"><div class="toolbar"><div><b id="pageTitle">Обзор</b><div id="freshness" class="muted"></div></div><div class="row"><button class="secondary" onclick="reloadCurrent(this)">Обновить</button><button data-global-action="scheduler" class="green" onclick="runScheduler(this)">Запустить проверки</button></div></div><div id="message" class="muted" role="status" aria-live="polite"></div><div id="view" class="card"><div class="loading">Загрузка…</div></div></main>
<aside class="side"><h3>Карточка дела</h3><div id="side" class="muted">Откройте дело из очереди или списка.</div><div class="side-section"><span class="raw-toggle" onclick="toggleRaw()">Технический ответ API</span><pre id="raw" hidden>{}</pre></div></aside>
</div>
<script>
let currentTab='dashboard',currentQueue='unassigned',currentCase=null,loadController=null;const pending=new Set();
const view=document.getElementById('view'),side=document.getElementById('side'),raw=document.getElementById('raw'),message=document.getElementById('message'),freshness=document.getElementById('freshness'),pageTitle=document.getElementById('pageTitle');let apiToken='';
const titles={dashboard:'Обзор',queue:'Рабочие очереди',cases:'Все дела',documents:'Документы',lawyers:'Юристы',notifications:'Уведомления',payments:'Платежи',settings:'Настройки'};
function esc(v){return String(v??'').replace(/[&<>\x22\x27]/g,c=>c==='&'?'&amp;':c==='<'?'&lt;':c==='>'?'&gt;':c.charCodeAt(0)===34?'&quot;':'&#39;')}
function feedback(text,state='muted'){message.textContent=text;message.className=state}
function compactError(e){return e&&e.message?e.message:String(e)}
function toggleRaw(){raw.hidden=!raw.hidden}
function loading(text='Загрузка…'){view.innerHTML=`<div class="loading">${esc(text)}</div>`}
function empty(title,detail=''){return `<div class="empty"><b>${esc(title)}</b>${detail?`<p>${esc(detail)}</p>`:''}</div>`}
function errorState(e){view.innerHTML=`<div class="error"><b>Не удалось загрузить раздел</b><p>${esc(compactError(e))}</p><button onclick="loadCurrent()">Повторить</button></div>`}
function formatDate(v){if(!v)return '—';try{return new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short'}).format(new Date(v))}catch{return v}}
function rub(v){const n=Number(v);return Number.isFinite(n)?new Intl.NumberFormat('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2}).format(n)+' ₽':'—'}
async function api(path,opts={}){if(!apiToken)throw new Error('Персональная admin-сессия не загружена');const r=await fetch(path,{...opts,credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':apiToken,'Content-Type':'application/json',...(opts.headers||{})}});if(r.status===401){location.href='/login';throw new Error('Сессия истекла')}if(r.status===403){location.href='/login';throw new Error('Недостаточно прав или сессия истекла')}const data=await r.json().catch(()=>({}));raw.textContent=JSON.stringify(data,null,2);if(!r.ok){const e=new Error(data.detail||'Ошибка запроса');e.status=r.status;throw e}return data}
function controls(selector){return selector?Array.from(document.querySelectorAll(selector)):[]}
async function withAction(key,button,selector,work,label='Выполняется…'){if(pending.has(key))return;pending.add(key);const items=selector?controls(selector):(button?[button]:[]);const labels=new Map(items.filter(x=>x.tagName==='BUTTON').map(x=>[x,x.textContent]));items.forEach(x=>{x.disabled=true;x.setAttribute('aria-busy','true')});if(button)button.textContent=label;try{return await work()}finally{pending.delete(key);items.forEach(x=>{x.disabled=false;x.removeAttribute('aria-busy')});labels.forEach((value,x)=>{x.textContent=value})}}
async function boot(){try{const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const session=await r.json();if(!(session.roles||[session.role]).includes('admin')){throw new Error('Требуется роль администратора')}apiToken=session.api_token||'';await loadCurrent()}catch(e){errorState(e)}}
function showTab(name,button){currentTab=name;pageTitle.textContent=titles[name]||name;document.querySelectorAll('.nav-button').forEach(x=>x.classList.toggle('active',x===button));side.innerHTML='Откройте дело из очереди или списка.';void loadCurrent()}
async function reloadCurrent(button){return withAction('reload:'+currentTab,button,null,()=>loadCurrent(),'Обновление…')}
async function loadCurrent(){if(loadController)loadController.abort();const controller=new AbortController();loadController=controller;loading();feedback('');try{if(currentTab==='dashboard')await loadDashboard(controller);else if(currentTab==='queue')await loadQueue(currentQueue,controller);else if(currentTab==='cases')await loadCases(controller);else if(currentTab==='documents')await loadDocuments(controller);else if(currentTab==='lawyers')await loadLawyers(controller);else if(currentTab==='notifications')await loadNotifications(controller);else if(currentTab==='payments')await loadPayments(controller);else await loadSettings(controller)}catch(e){if(e.name!=='AbortError')errorState(e)}finally{if(loadController===controller)loadController=null}}
function queueCard(key,title,count,detail,urgent=false){return `<button class="queue-card ${urgent?'urgent':''}" onclick="openQueue('${key}')"><span class="count">${count||0}</span><b>${esc(title)}</b><div class="muted">${esc(detail)}</div></button>`}
async function loadDashboard(controller){const d=await api('/admin/dashboard',{signal:controller.signal});freshness.textContent='Обновлено '+formatDate(d.generated_at);view.innerHTML=`<div class="metric-grid"><button class="metric" onclick="showCasesTab()"><b>${d.cases?.active||0}</b><span>активных дел</span></button><button class="metric warn" onclick="openQueue('unassigned')"><b>${d.queue?.unassigned||0}</b><span>без юриста</span></button><button class="metric ${d.queue?.overdue?'urgent':''}" onclick="openQueue('overdue')"><b>${d.queue?.overdue||0}</b><span>SLA-просрочек</span></button><button class="metric" onclick="openQueue('consultations')"><b>${d.consultations?.today||0}</b><span>консультаций сегодня</span></button></div><div class="section-title"><div><h2>Что требует внимания</h2><p>Откройте очередь и выполните ближайшее действие</p></div></div><div class="queue-grid">${queueCard('unassigned','Назначить юриста',d.queue?.unassigned,'Новые дела без ответственного')}${queueCard('documents','Проверить документы',d.queue?.documents_review,'Только пакеты, которые клиент передал юристу')}${queueCard('consultations','Консультации сегодня',d.queue?.consultations_today,'Подготовка и проведение консультаций')}${queueCard('overdue','Просроченные действия',d.queue?.overdue,'Требуется немедленная реакция',Boolean(d.queue?.overdue))}</div>`}
function showCasesTab(){const button=document.querySelector('[data-tab="cases"]');showTab('cases',button)}
function openQueue(name){currentQueue=name;const button=document.querySelector('[data-tab="queue"]');showTab('queue',button)}
const queueTitles={unassigned:'Дела без юриста',documents:'Документы на проверке',consultations:'Консультации сегодня',overdue:'Просроченные действия'};
async function loadQueue(name,controller){const d=await api('/admin/work-queues/'+encodeURIComponent(name),{signal:controller.signal});freshness.textContent='Обновлено '+formatDate(d.generated_at);const tabs=Object.entries(queueTitles).map(([key,title])=>`<button class="${key===name?'':'secondary'}" onclick="openQueue('${key}')">${esc(title)}</button>`).join(' ');const items=(d.items||[]).map(x=>`<article class="list-item"><div class="list-head"><div><b>${esc(x.number)}</b> · ${esc(x.route_label||x.route||'—')}<div class="muted">Создано ${formatDate(x.created_at)}</div></div><span class="badge ${x.sla_status?.includes('OVERDUE')?'red':''}">${esc(x.status_label||x.status)}</span></div><p>${esc(x.next_action||'Определить следующее действие')}</p><div class="muted">Ответственный: ${esc(x.lawyer_name||'не назначен')} · ${esc(x.sla_label||x.sla_status||'SLA не запущен')} ${x.sla_due_at?'до '+formatDate(x.sla_due_at):''}</div><div class="row" style="margin-top:10px"><button onclick="openCase(${x.id})">Открыть дело</button>${name==='documents'?`<a class="button" href="/admin/workdesk/cases/${x.id}/action/documents">Проверить документы</a>`:''}${!x.lawyer_id?`<button class="green" data-case-id="${x.id}" data-expected-status="${esc(x.status)}" data-expected-lawyer="" onclick="autoAssign(${x.id},this)">Автоназначить</button>`:''}</div></article>`).join('');view.innerHTML=`<div class="row">${tabs}</div><div class="section-title"><div><h2>${esc(queueTitles[name])}</h2><p>${d.count} элементов</p></div></div>${items||empty('Очередь пуста','На текущий момент действий в этой категории нет.')}`}
async function loadCases(controller){const rows=await api('/admin/cases',{signal:controller.signal});freshness.textContent='';if(!rows.length){view.innerHTML=empty('Дел пока нет');return}view.innerHTML='<table><tr><th>Дело</th><th>Маршрут</th><th>Статус</th><th>Юрист</th><th>Действие</th></tr>'+rows.map(x=>`<tr><td><b>${esc(x.number)}</b><br><span class="muted">#${x.id}</span></td><td>${esc(x.route||'—')}</td><td><span class="badge">${esc(x.status)}</span></td><td>${esc(x.lawyer_id||'не назначен')}</td><td><button onclick="openCase(${x.id})">Открыть</button></td></tr>`).join('')+'</table>'}
async function openCase(id){try{side.innerHTML='<div class="loading">Загрузка карточки…</div>';const d=await api('/admin/case-workspace/'+id);currentCase=d.case;const docs=(d.documents||[]).map(x=>`<div class="list-item"><b>${esc(x.title)}</b><div class="muted">${esc(x.status_label||x.status)} · версия ${esc(x.version||1)}</div>${x.lawyer_comment?`<div class="warn-text">Комментарий: ${esc(x.lawyer_comment)}</div>`:''}</div>`).join('')||empty('Документов нет');const payments=(d.payments||[]).map(p=>`<div class="list-item"><b>${esc(p.title)}</b><div class="muted">${rub(p.amount)} · ${esc(p.status)}</div>${p.manual_confirm_allowed?`<button class="green" data-payment-id="${p.id}" data-case-id="${p.case_id}" data-expected-status="${esc(p.status)}" onclick="confirmPayment(${p.id},${p.case_id},this)">Подтвердить тестовый платёж</button>`:''}${p.offline_confirm_allowed?`<div style="margin-top:8px"><a class="button green" href="/admin/cases/${id}/ui">Подтвердить поступление</a></div>`:''}</div>`).join('');const statusOptions=d.case.manual_status_options||[];const statusAction=statusOptions.length?`<button data-case-id="${id}" onclick="showStatusForm(${id},this)">Изменить статус</button>`:(d.case.manual_status_note?`<div class="action-box"><b>Статус защищён</b>${esc(d.case.manual_status_note)}</div>`:'');const finance=d.financial_final;const financeBox=finance?.applicable?`<div class="side-section"><h4>Финансовый финал M1</h4><div class="data-grid"><div class="data-cell"><span>Фактически взыскано</span>${rub(finance.recovered_amount)}</div><div class="data-cell"><span>Success fee</span>${rub(finance.expected_success_fee)}</div></div><div class="${finance.health==='critical'?'bad':finance.health==='warning'?'warn-text':'ok'}" style="margin-top:8px">${finance.health==='critical'?'Есть несогласованность':finance.health==='warning'?'Требует внимания':'Контур согласован'}</div><div class="muted" style="margin-top:5px">${esc(finance.recommended_action||'')}</div><div class="row" style="margin-top:9px"><a class="button secondary" href="/admin/cases/${id}/ui">Открыть финансовую карточку</a></div></div>`:'';side.innerHTML=`<h3>${esc(d.case.number)}</h3><span class="badge ${d.case.sla_status?.includes('OVERDUE')?'red':''}">${esc(d.case.status_label||d.case.status)}</span><div class="action-box"><b>Рекомендуемое действие</b>${esc(d.case.next_action||'Определить следующий шаг')}</div><div class="data-grid"><div class="data-cell"><span>Услуга</span>${esc(d.case.route_label||d.case.route||'—')}</div><div class="data-cell"><span>Ответственный</span>${esc(d.case.lawyer_name||'не назначен')}</div><div class="data-cell"><span>SLA</span>${esc(d.case.sla_label||'—')}</div><div class="data-cell"><span>Срок</span>${formatDate(d.case.sla_due_at)}</div></div>${d.client?`<div class="side-section"><h4>Клиент</h4><div>${esc(d.client.name||'Имя не указано')}</div><div class="muted">${d.client.username?'@'+esc(d.client.username):'Telegram username не указан'}</div></div>`:''}<div class="row" style="margin-top:12px"><a class="button secondary" href="/admin/cases/${id}/ui">Подробная карточка</a>${statusOptions.length?statusAction:''}${d.case.lawyer_id?'':`<button class="green" data-case-id="${id}" data-expected-status="${esc(d.case.status)}" data-expected-lawyer="" onclick="autoAssign(${id},this)">Автоназначить</button>`}</div>${!statusOptions.length&&d.case.manual_status_note?statusAction:''}${financeBox}<div class="side-section"><h4>Документы</h4>${docs}</div>${payments?`<div class="side-section"><h4>Платежи</h4>${payments}</div>`:''}`;return d}catch(e){side.innerHTML=`<div class="error">${esc(compactError(e))}<br><button onclick="openCase(${id})">Повторить</button></div>`}}
async function showStatusForm(id){try{const d=currentCase&&currentCase.id===id?{case:currentCase}:await openCase(id);const c=d.case||currentCase,options=c?.manual_status_options||[];if(!options.length){feedback(c?.manual_status_note||'Для текущего этапа нет допустимых ручных переходов. Используйте профильный сценарий.','warn-text');return}side.insertAdjacentHTML('beforeend',`<div class="side-section"><h4>Изменить статус</h4><div class="muted">Показаны только серверно разрешённые административные переходы.</div><select id="newStatus">${options.map(s=>`<option value="${esc(s)}">${esc(s)}</option>`).join('')}</select><textarea id="statusComment" placeholder="Причина изменения — минимум 5 символов"></textarea><button data-case-id="${id}" data-expected-status="${esc(c.status)}" onclick="saveStatus(${id},this)">Сохранить</button></div>`)}catch(e){feedback('Форма статуса не загружена: '+compactError(e),'bad')}}
async function saveStatus(id,button){const next=document.getElementById('newStatus')?.value||'',comment=(document.getElementById('statusComment')?.value||'').trim(),expected=button.dataset.expectedStatus||'';if(!next){feedback('Нет допустимого нового статуса','bad');return}if(comment.length<5){feedback('Укажите причину изменения — не менее 5 символов','bad');return}if(next===expected){feedback('Выберите новый статус','bad');return}if(!confirm(`Изменить статус ${expected} → ${next}?`))return;return withAction('case:'+id,button,`[data-case-id="${id}"]`,async()=>{try{const result=await api('/admin/cases/'+id+'/status',{method:'POST',body:JSON.stringify({status:next,comment,expected_status:expected})});feedback(`Статус дела #${id} сохранён: ${result.status}`,'ok');try{await openCase(id);await loadCurrent()}catch(e){feedback('Статус сохранён, но экран не обновился: '+compactError(e),'warn-text')}}catch(e){feedback('Статус не изменён: '+compactError(e),'bad')}},'Сохранение…')}
async function autoAssign(id,button){const expectedStatus=button.dataset.expectedStatus||'',expectedLawyer=button.dataset.expectedLawyer||null;if(!confirm('Автоматически назначить подходящего юриста? Это запустит SLA первой реакции.'))return;return withAction('case:'+id,button,`[data-case-id="${id}"]`,async()=>{try{const result=await api('/admin/cases/'+id+'/auto-assign',{method:'POST',body:JSON.stringify({expected_status:expectedStatus,expected_lawyer_id:expectedLawyer})});feedback(`Дело #${id} назначено: ${result.lawyer||result.lawyer_id}`,'ok');try{await openCase(id);await loadCurrent()}catch(e){feedback('Дело назначено, но экран не обновился: '+compactError(e),'warn-text')}}catch(e){feedback('Дело не назначено: '+compactError(e),'bad')}},'Назначение…')}
async function loadDocuments(controller){const rows=await api('/admin/documents',{signal:controller.signal});if(!rows.length){view.innerHTML=empty('Документов пока нет');return}view.innerHTML='<table><tr><th>Дело</th><th>Тип</th><th>Файл</th><th>Статус</th></tr>'+rows.map(x=>`<tr><td><button onclick="openCase(${x.case_id})">#${x.case_id}</button></td><td>${esc(x.type)}</td><td>${esc(x.file_name)}</td><td><span class="badge">${esc(x.status)}</span></td></tr>`).join('')+'</table>'}
async function loadPayments(controller){const rows=await api('/admin/payments',{signal:controller.signal});if(!rows.length){view.innerHTML=empty('Платежей нет','В режиме без онлайн-оплаты это нормальное состояние.');return}view.innerHTML='<table><tr><th>ID</th><th>Дело</th><th>Назначение</th><th>Сумма</th><th>Статус</th><th>Действие</th></tr>'+rows.map(p=>`<tr><td>${p.id}</td><td><button onclick="openCase(${p.case_id})">#${p.case_id}</button></td><td>${esc(p.title)}</td><td>${p.amount}</td><td>${esc(p.status)}</td><td>${p.manual_confirm_allowed?`<button class="green" data-payment-id="${p.id}" data-case-id="${p.case_id}" data-expected-status="${esc(p.status)}" onclick="confirmPayment(${p.id},${p.case_id},this)">Подтвердить тестовый платёж</button>`:p.offline_confirm_allowed?`<a class="button green" href="/admin/cases/${p.case_id}/ui">Подтвердить поступление</a>`:'—'}</td></tr>`).join('')+'</table>'}
async function confirmPayment(paymentId,caseId,button){const expected=button.dataset.expectedStatus||'';if(!confirm('Подтвердить тестовый платёж? В production эта операция недоступна.'))return;return withAction('payment:'+paymentId,button,`[data-payment-id="${paymentId}"]`,async()=>{try{const result=await api('/admin/payments/'+paymentId+'/confirm',{method:'POST',body:JSON.stringify({expected_status:expected})});feedback(`Платёж #${result.payment_id} подтверждён: ${result.status}`,'ok');try{if(caseId)await openCase(caseId);await loadCurrent()}catch(e){feedback('Платёж подтверждён, но экран не обновился: '+compactError(e),'warn-text')}}catch(e){feedback('Платёж не подтверждён: '+compactError(e),'bad')}},'Подтверждение…')}
async function loadLawyers(controller){const rows=await api('/admin/lawyers',{signal:controller.signal});view.innerHTML='<div class="row"><button data-global-action="create-lawyer" onclick="showLawyerForm()">Добавить юриста</button></div>'+(rows.length?'<table><tr><th>ФИО</th><th>Email</th><th>Статус</th><th>Лимит</th></tr>'+rows.map(x=>`<tr><td>${esc(x.full_name)}</td><td>${esc(x.email||'—')}</td><td>${x.is_active?'активен':'отключён'}</td><td>${x.workload_limit}</td></tr>`).join('')+'</table>':empty('Юристы не добавлены'))}
function showLawyerForm(){side.innerHTML=`<h3>Новый юрист</h3><input id="lawyerName" placeholder="ФИО"><input id="lawyerEmail" type="email" placeholder="Email"><input id="lawyerPhone" placeholder="Телефон"><input id="lawyerSpec" placeholder="Специализация"><input id="lawyerLimit" type="number" min="1" max="500" value="30"><button data-global-action="create-lawyer" onclick="createLawyer(this)">Создать</button>`}
async function createLawyer(button){const full_name=(document.getElementById('lawyerName')?.value||'').trim(),email=(document.getElementById('lawyerEmail')?.value||'').trim().toLowerCase(),workload_limit=Number(document.getElementById('lawyerLimit')?.value||0);if(full_name.length<3){feedback('Укажите ФИО юриста','bad');return}if(!email.includes('@')){feedback('Укажите корректный email','bad');return}if(!Number.isInteger(workload_limit)||workload_limit<1||workload_limit>500){feedback('Лимит должен быть от 1 до 500','bad');return}return withAction('global:create-lawyer',button,'[data-global-action="create-lawyer"]',async()=>{try{const result=await api('/admin/lawyers',{method:'POST',body:JSON.stringify({full_name,email,phone:document.getElementById('lawyerPhone')?.value||'',specialization:document.getElementById('lawyerSpec')?.value||'',workload_limit})});feedback(`Юрист ${result.full_name} создан`,'ok');side.innerHTML='Юрист создан.';try{await loadCurrent()}catch(e){feedback('Юрист создан, но список не обновился: '+compactError(e),'warn-text')}}catch(e){feedback('Юрист не создан: '+compactError(e),'bad')}},'Создание…')}
async function loadNotifications(controller){const rows=await api('/admin/notifications',{signal:controller.signal});view.innerHTML=rows.length?rows.map(x=>`<article class="list-item"><b>${esc(x.title)}</b> · <span class="badge">${esc(x.status)}</span><p>${esc(x.text)}</p><div class="muted">${esc(x.event)} · дело ${esc(x.case_id||'—')}</div></article>`).join(''):empty('Новых уведомлений нет')}
async function loadSettings(controller){const rows=await api('/admin/settings',{signal:controller.signal});view.innerHTML=rows.length?rows.map((r,i)=>`<article class="list-item"><b>${esc(r.title)}</b><div class="muted">${esc(r.key)} · ${formatDate(r.updated_at)}</div><input id="setting_${i}" value="${esc(r.value?.value??'')}" ${r.editable?'':'disabled'}>${r.editable?`<button data-setting-key="${esc(r.key)}" data-input-id="setting_${i}" data-expected-updated-at="${esc(r.updated_at)}" onclick="saveSetting(this)">Сохранить</button>`:''}</article>`).join(''):empty('Настройки не найдены')}
async function saveSetting(button){const key=button.dataset.settingKey,input=document.getElementById(button.dataset.inputId),expected=button.dataset.expectedUpdatedAt;if(!key||!input){feedback('Настройка не найдена на странице','bad');return}if(!confirm('Сохранить системную настройку '+key+'?'))return;return withAction('setting:'+key,button,`[data-setting-key="${CSS.escape(key)}"]`,async()=>{try{const result=await api('/admin/settings/'+encodeURIComponent(key),{method:'POST',body:JSON.stringify({value:input.value,expected_updated_at:expected})});button.dataset.expectedUpdatedAt=result.updated_at;feedback('Настройка сохранена','ok');try{await loadCurrent()}catch(e){feedback('Настройка сохранена, но список не обновился: '+compactError(e),'warn-text')}}catch(e){feedback('Настройка не сохранена: '+compactError(e),'bad')}},'Сохранение…')}
async function runScheduler(button){if(!confirm('Запустить все плановые проверки сейчас? Операция может изменить SLA и рабочие очереди.'))return;return withAction('global:scheduler',button,'[data-global-action="scheduler"]',async()=>{try{const result=await api('/admin/scheduler/run-once',{method:'POST',body:'{}'});feedback(`Плановые проверки завершены: ${Object.keys(result||{}).length} блоков результата`,'ok');try{await loadCurrent()}catch(e){feedback('Плановые проверки завершены, но раздел не обновился: '+compactError(e),'warn-text')}}catch(e){feedback('Плановые проверки не выполнены: '+compactError(e),'bad')}},'Проверка…')}
boot();
</script>
</body>
</html>
"""
