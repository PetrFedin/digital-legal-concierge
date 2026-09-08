from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.consultations.consultation_intake import consultation_description_ready
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.document_statuses import DocumentStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.document import Document
from app.models.user import User
from app.presentation_time import to_business_timezone
from app.security.lawyer_access import require_lawyer_actor

router = APIRouter(prefix="/lawyer/consultation-desk", tags=["lawyer-consultation-desk"])

CLOSED_CONSULTATION_STATUSES = {
    ConsultationStatus.DONE,
    ConsultationStatus.CANCELLED,
    ConsultationStatus.CLOSED,
}
DOCUMENT_STATUS_LABELS = {
    DocumentStatus.REQUIRED: "Ожидается",
    DocumentStatus.UPLOADED: "Загружен",
    DocumentStatus.ON_REVIEW: "На проверке",
    DocumentStatus.APPROVED: "Принят",
    DocumentStatus.REJECTED: "Отклонён",
    DocumentStatus.NEEDS_REUPLOAD: "Нужна новая версия",
    DocumentStatus.ARCHIVED: "Архив",
}
PREBOOKING_STATES = {
    ConsultationStatus.DESCRIPTION_PENDING: (
        "question_pending",
        "Ожидается вопрос клиента",
        "После описания вопроса клиент сможет добавить материалы и выбрать время.",
    ),
    ConsultationStatus.DOCUMENTS_OPTIONAL: (
        "materials_pending",
        "Клиент готовит материалы",
        "Документы необязательны; время станет следующим шагом клиента.",
    ),
    ConsultationStatus.SLOT_PENDING: (
        "slot_pending",
        "Клиент выбирает время",
        "Свяжитесь с клиентом только при необходимости помочь с записью.",
    ),
    ConsultationStatus.SLOT_RESERVED: (
        "confirmation_pending",
        "Время зарезервировано",
        "Ожидается подтверждение записи или оплаты.",
    ),
    ConsultationStatus.PAYMENT_PENDING: (
        "confirmation_pending",
        "Ожидается подтверждение",
        "Не проводите консультацию до подтверждения записи.",
    ),
}


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _business_date(value: datetime) -> object:
    return to_business_timezone(value).date()


def _consultation_status(value: object) -> ConsultationStatus | None:
    try:
        return ConsultationStatus(str(value))
    except (TypeError, ValueError):
        return None


def _document_status(value: object) -> DocumentStatus | None:
    try:
        return DocumentStatus(str(value))
    except (TypeError, ValueError):
        return None


def _latest_documents(documents: list[Document]) -> list[Document]:
    latest: dict[str, Document] = {}
    active = [
        document
        for document in documents
        if _document_status(document.status) != DocumentStatus.ARCHIVED
    ]
    for document in sorted(
        active,
        key=lambda item: (
            str(item.document_type or ""),
            -int(item.version or 0),
            -int(item.id or 0),
        ),
    ):
        latest.setdefault(str(document.document_type or "OTHER"), document)
    return sorted(
        latest.values(),
        key=lambda item: (item.created_at, item.id),
        reverse=True,
    )


def _duration_label(delta: timedelta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return "меньше минуты"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин."
    hours, remainder = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} ч. {remainder} мин." if remainder else f"{hours} ч."
    days, remainder_hours = divmod(hours, 24)
    return (
        f"{days} д. {remainder_hours} ч."
        if remainder_hours
        else f"{days} д."
    )


def _timing_payload(
    consultation: Consultation,
    slot: ConsultationSlot | None,
    now: datetime,
) -> dict[str, object]:
    status = _consultation_status(consultation.status)
    scheduled_at = _as_utc(consultation.scheduled_at)
    display_start = scheduled_at or _as_utc(slot.starts_at if slot else None)
    display_end = _as_utc(slot.ends_at if slot else None)
    if display_start and not display_end:
        display_end = display_start + timedelta(hours=1)
    business_today = _business_date(now)

    if status in PREBOOKING_STATES:
        state, label, detail = PREBOOKING_STATES[status]
        return {
            "state": state,
            "label": label,
            "detail": detail,
            "scheduled_at": display_start.isoformat() if display_start else None,
            "ends_at": display_end.isoformat() if display_end else None,
            "can_complete": False,
            "can_mark_no_show": False,
            "is_today": False,
            "requires_outcome": False,
        }

    if status == ConsultationStatus.CLIENT_NO_SHOW:
        return {
            "state": "client_no_show",
            "label": "Неявка клиента зафиксирована",
            "detail": "Дальнейший перенос или закрытие выполняется через администратора.",
            "scheduled_at": display_start.isoformat() if display_start else None,
            "ends_at": display_end.isoformat() if display_end else None,
            "can_complete": False,
            "can_mark_no_show": False,
            "is_today": bool(
                display_start and _business_date(display_start) == business_today
            ),
            "requires_outcome": False,
        }

    if status == ConsultationStatus.LAWYER_NO_SHOW:
        return {
            "state": "lawyer_no_show",
            "label": "Требуется восстановить сервис",
            "detail": "Свяжитесь с клиентом; перенос или возврат оформляет администратор.",
            "scheduled_at": display_start.isoformat() if display_start else None,
            "ends_at": display_end.isoformat() if display_end else None,
            "can_complete": False,
            "can_mark_no_show": False,
            "is_today": bool(
                display_start and _business_date(display_start) == business_today
            ),
            "requires_outcome": False,
        }

    if status != ConsultationStatus.BOOKED:
        return {
            "state": "status_review",
            "label": "Статус требует проверки",
            "detail": "Обновите кабинет или уточните состояние записи у администратора.",
            "scheduled_at": display_start.isoformat() if display_start else None,
            "ends_at": display_end.isoformat() if display_end else None,
            "can_complete": False,
            "can_mark_no_show": False,
            "is_today": bool(
                display_start and _business_date(display_start) == business_today
            ),
            "requires_outcome": False,
        }

    if scheduled_at is None:
        return {
            "state": "schedule_missing",
            "label": "Время записи не определено",
            "detail": "Не фиксируйте результат: сначала восстановите время записи через администратора.",
            "scheduled_at": display_start.isoformat() if display_start else None,
            "ends_at": display_end.isoformat() if display_end else None,
            "can_complete": False,
            "can_mark_no_show": False,
            "is_today": False,
            "requires_outcome": False,
        }

    is_today = _business_date(scheduled_at) == business_today
    no_show_available_at = scheduled_at + timedelta(minutes=15)
    if now < scheduled_at:
        return {
            "state": "upcoming",
            "label": "Подготовка к консультации",
            "detail": f"До начала: {_duration_label(scheduled_at - now)}",
            "scheduled_at": scheduled_at.isoformat(),
            "ends_at": display_end.isoformat() if display_end else None,
            "can_complete": False,
            "can_mark_no_show": False,
            "is_today": is_today,
            "requires_outcome": False,
        }

    if display_end and now < display_end:
        can_mark_no_show = now >= no_show_available_at
        return {
            "state": "in_progress",
            "label": "Консультация идёт",
            "detail": (
                "Зафиксируйте итог после разговора."
                if can_mark_no_show
                else "Неявку можно отметить через 15 минут после начала."
            ),
            "scheduled_at": scheduled_at.isoformat(),
            "ends_at": display_end.isoformat(),
            "can_complete": True,
            "can_mark_no_show": can_mark_no_show,
            "is_today": is_today,
            "requires_outcome": False,
        }

    return {
        "state": "outcome_due",
        "label": "Нужно зафиксировать результат",
        "detail": "Консультация завершилась по времени; выберите итоговое решение.",
        "scheduled_at": scheduled_at.isoformat(),
        "ends_at": display_end.isoformat() if display_end else None,
        "can_complete": True,
        "can_mark_no_show": now >= no_show_available_at,
        "is_today": is_today,
        "requires_outcome": True,
    }


def _document_payload(document: Document) -> dict[str, object]:
    status = _document_status(document.status)
    return {
        "title": document.title,
        "document_type": document.document_type,
        "version": int(document.version or 1),
        "status": str(document.status),
        "status_label": DOCUMENT_STATUS_LABELS.get(status, "Статус уточняется"),
        "created_at": document.created_at.isoformat(),
    }


def _checklist(
    *,
    question_ready: bool,
    scheduled: bool,
    documents: list[Document],
    documents_on_review: int,
) -> list[dict[str, str]]:
    if documents:
        documents_state = "pending" if documents_on_review else "ready"
        documents_detail = (
            f"На проверке: {documents_on_review}"
            if documents_on_review
            else f"Доступно материалов: {len(documents)}"
        )
    else:
        documents_state = "optional"
        documents_detail = "Клиент не добавил материалы; для консультации это допустимо."
    return [
        {
            "code": "question",
            "label": "Вопрос клиента",
            "state": "ready" if question_ready else "pending",
            "detail": "Сохранён и доступен юристу" if question_ready else "Ожидается описание",
        },
        {
            "code": "schedule",
            "label": "Дата и время",
            "state": "ready" if scheduled else "pending",
            "detail": "Запись подтверждена" if scheduled else "Время ещё не подтверждено",
        },
        {
            "code": "documents",
            "label": "Материалы",
            "state": documents_state,
            "detail": documents_detail,
        },
        {
            "code": "contact",
            "label": "Связь с клиентом",
            "state": "ready",
            "detail": "Диалог доступен из карточки",
        },
    ]


@router.get("/data")
async def consultation_desk_data(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await require_lawyer_actor(db, x_admin_token)
    rows = (
        await db.execute(
            select(Consultation, Case, User, ConsultationSlot)
            .join(Case, Case.id == Consultation.case_id)
            .join(User, User.id == Case.client_id)
            .outerjoin(ConsultationSlot, ConsultationSlot.id == Consultation.slot_id)
            .where(Consultation.lawyer_id == actor.lawyer.id)
            .where(Consultation.status.notin_(CLOSED_CONSULTATION_STATUSES))
            .order_by(
                Consultation.scheduled_at.asc(),
                Consultation.created_at.desc(),
            )
            .limit(300)
        )
    ).all()

    case_ids = sorted({case.id for _, case, _, _ in rows})
    documents_by_case: dict[int, list[Document]] = defaultdict(list)
    if case_ids:
        documents = (
            await db.execute(
                select(Document)
                .where(Document.case_id.in_(case_ids))
                .where(Document.status != DocumentStatus.ARCHIVED)
                .order_by(Document.created_at.desc(), Document.id.desc())
            )
        ).scalars().all()
        for document in documents:
            documents_by_case[document.case_id].append(document)

    now = datetime.now(timezone.utc)
    items: list[dict[str, object]] = []
    for consultation, case, user, slot in rows:
        timing = _timing_payload(consultation, slot, now)
        latest_documents = _latest_documents(documents_by_case.get(case.id, []))
        documents_on_review = sum(
            1
            for document in latest_documents
            if _document_status(document.status) == DocumentStatus.ON_REVIEW
        )
        question_ready = consultation_description_ready(consultation)
        checklist = _checklist(
            question_ready=question_ready,
            scheduled=bool(timing["scheduled_at"]),
            documents=latest_documents,
            documents_on_review=documents_on_review,
        )
        items.append(
            {
                "consultation_id": consultation.id,
                "case_id": case.id,
                "case_number": case.case_number,
                "client_name": user.full_name,
                "status": str(consultation.status),
                "updated_at": consultation.updated_at.isoformat(),
                "question": consultation.client_description,
                "question_ready": question_ready,
                "documents_count": len(latest_documents),
                "documents_on_review": documents_on_review,
                "documents": [_document_payload(item) for item in latest_documents[:6]],
                "checklist": checklist,
                "ready_steps": sum(1 for item in checklist if item["state"] == "ready"),
                "total_steps": len(checklist),
                "case_url": f"/lawyer/workspace/ui?case_id={case.id}",
                "message_url": f"/message-center/ui?case_id={case.id}",
                "document_review_url": f"/document-access/review/ui?case_id={case.id}",
                **timing,
            }
        )

    priority = {
        "outcome_due": 0,
        "in_progress": 1,
        "lawyer_no_show": 2,
        "upcoming": 3,
        "schedule_missing": 4,
        "status_review": 5,
        "client_no_show": 6,
    }
    items.sort(
        key=lambda item: (
            priority.get(str(item["state"]), 7),
            str(item.get("scheduled_at") or "9999"),
            -int(item["consultation_id"]),
        )
    )
    return {
        "generated_at": now.isoformat(),
        "business_timezone": settings.business_timezone,
        "business_timezone_label": settings.business_timezone_label,
        "lawyer": {"id": actor.lawyer.id, "name": actor.lawyer.full_name},
        "summary": {
            "active": len(items),
            "today": sum(1 for item in items if item["is_today"]),
            "requires_outcome": sum(1 for item in items if item["requires_outcome"]),
            "documents_on_review": sum(
                int(item["documents_on_review"]) for item in items
            ),
        },
        "consultations": items,
    }


@router.get("/ui", response_class=HTMLResponse)
async def consultation_desk_ui():
    return HTMLResponse(CONSULTATION_DESK_HTML)


CONSULTATION_DESK_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Консультации — Digital Legal Concierge</title>
<style>
:root{--bg:#f4f6fa;--surface:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--primary:#3157d5;--primary-soft:#eef2ff;--green:#14804a;--green-soft:#ecfdf3;--amber:#a15c00;--amber-soft:#fff7e6;--red:#b42318;--red-soft:#fef3f2;--shadow:0 14px 38px rgba(16,24,40,.08)}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}header{position:sticky;top:0;z-index:20;background:linear-gradient(135deg,#101828,#293b66);color:#fff;padding:18px 24px;box-shadow:0 8px 24px rgba(16,24,40,.14)}header .inner{max-width:1280px;margin:auto;display:flex;justify-content:space-between;align-items:center;gap:18px}h1{font-size:23px;margin:0 0 4px}header p{margin:0;color:#d0d5dd;font-size:13px}.links,.actions,.tabs{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.button,button{border:0;border-radius:10px;padding:10px 13px;background:var(--primary);color:#fff;font-weight:750;cursor:pointer;text-decoration:none;display:inline-block;font:inherit}.secondary{background:#475467}.ghost{background:#fff;color:var(--ink);border:1px solid var(--line)}.green{background:var(--green)}.danger{background:var(--red)}button:disabled,textarea:disabled,select:disabled{opacity:.55;cursor:wait}main{max-width:1280px;margin:auto;padding:22px}.welcome{display:flex;justify-content:space-between;align-items:end;gap:16px;margin-bottom:14px}.welcome h2{margin:0 0 4px}.muted{color:var(--muted);font-size:13px}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:11px}.metric{background:#fff;border:1px solid var(--line);border-radius:15px;padding:14px;box-shadow:var(--shadow)}.metric b{display:block;font-size:27px}.metric span{font-size:12px;color:var(--muted)}.metric.warn{background:var(--amber-soft);border-color:#fedf89}.toolbar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:20px 0 12px}.tabs button{background:#fff;color:var(--ink);border:1px solid var(--line)}.tabs button.active{background:var(--primary);border-color:var(--primary);color:#fff}.search{min-width:280px;max-width:420px;flex:1}.search input{width:100%;border:1px solid #d0d5dd;border-radius:11px;background:#fff;padding:11px 12px;font:inherit}.match{margin-top:5px;color:var(--muted);font-size:12px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.card{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:17px;box-shadow:var(--shadow)}.card.outcome_due{border-color:#f79009}.card.in_progress{border-color:#84adff}.card.lawyer_no_show{border-color:#fda29b;background:#fffafa}.card-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.card h3{margin:0 0 4px;font-size:18px}.badge{display:inline-flex;border-radius:999px;padding:6px 9px;background:#eef2f6;font-size:12px;font-weight:750}.badge.upcoming{background:var(--primary-soft);color:var(--primary)}.badge.in_progress{background:#eff8ff;color:#175cd3}.badge.outcome_due,.badge.confirmation_pending{background:var(--amber-soft);color:var(--amber)}.badge.lawyer_no_show,.badge.schedule_missing{background:var(--red-soft);color:var(--red)}.section-label{margin:15px 0 6px;color:var(--muted);font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.08em}.now-box,.next-box{border-radius:14px;padding:13px}.now-box{background:var(--primary-soft);border:1px solid #c7d2fe}.now-box b,.next-box b{display:block;margin-bottom:4px}.next-box{background:#f8fafc;border:1px solid var(--line)}.next-box.action{background:var(--green-soft);border-color:#abefc6}.next-box.attention{background:var(--amber-soft);border-color:#fedf89}.question{margin:10px 0;padding:13px;border-radius:13px;background:#f8fafc;line-height:1.5}.question span{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em;margin-bottom:5px}.checklist{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:10px 0}.check{border:1px solid var(--line);border-radius:11px;padding:10px;background:#fff}.check b{font-size:13px}.check small{display:block;color:var(--muted);margin-top:3px;line-height:1.35}.check.ready{border-color:#abefc6;background:var(--green-soft)}.check.pending{border-color:#fedf89;background:var(--amber-soft)}.check.optional{background:#f8fafc}.documents{border-top:1px solid var(--line);margin-top:12px;padding-top:10px}.documents summary,.exception summary{cursor:pointer;font-weight:750}.document{display:flex;justify-content:space-between;gap:10px;border-bottom:1px solid #f0f2f5;padding:8px 0;font-size:13px}.document:last-child{border-bottom:0}.document small{display:block;color:var(--muted)}.secondary-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;padding-top:12px;border-top:1px solid var(--line)}.exception{margin-top:12px;padding:10px 12px;border:1px solid #fecdca;border-radius:12px;background:#fffafa;color:var(--red)}.exception .danger{margin-top:9px}.form{display:none;border-top:1px solid var(--line);margin-top:13px;padding-top:13px}.form.open{display:block}.form textarea,.form select{width:100%;border:1px solid #d0d5dd;border-radius:10px;padding:10px;margin:5px 0;min-height:96px;font:inherit}.form select{min-height:auto}.form-help{font-size:12px;color:var(--muted);line-height:1.4;margin:5px 0 10px}.message{min-height:24px;margin:10px 0}.ok{color:var(--green)}.bad{color:var(--red)}.warn-text{color:var(--amber)}.empty,.error,.loading{grid-column:1/-1;padding:36px;text-align:center;background:#fff;border:1px dashed var(--line);border-radius:16px;color:var(--muted)}.empty .actions,.error .actions{justify-content:center;margin-top:12px}.error{background:var(--red-soft);color:var(--red)}@media(max-width:900px){.grid{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}}@media(max-width:680px){header{position:static}.inner,.welcome,.toolbar{align-items:flex-start;flex-direction:column}main{padding:14px}.checklist{grid-template-columns:1fr}.links{width:100%}.links .button{flex:1;text-align:center}.metrics{grid-template-columns:1fr 1fr}.search{width:100%;min-width:0}.secondary-actions .button,.secondary-actions button{flex:1;text-align:center}}
</style>
</head>
<body>
<header><div class="inner"><div><h1>⚖ Консультации</h1><p>Один главный следующий шаг по каждой встрече — без скрытых смен статуса</p></div><div class="links"><a class="button secondary" href="/lawyer/workspace/ui">Все дела</a><a class="button secondary" href="/message-center/ui">Сообщения</a><a class="button secondary" href="/operator">Все разделы</a><form method="post" action="/logout" style="margin:0"><button class="secondary" type="submit">Выйти</button></form></div></div></header>
<main>
<div class="welcome"><div><h2 id="lawyerName">Консультации</h2><div id="freshness" class="muted"></div></div><button class="secondary" onclick="load(this,false)">Обновить</button></div>
<div id="metrics" class="metrics"></div>
<div class="toolbar"><div class="tabs"><button class="active" onclick="showTab('attention',this)">Требуют действия</button><button onclick="showTab('today',this)">Сегодня</button><button onclick="showTab('all',this)">Все активные</button></div><div class="search"><input id="filter" type="search" placeholder="Найти по делу, клиенту или вопросу" oninput="render()"><div id="match" class="match"></div></div></div>
<div id="message" class="message" role="status" aria-live="polite"></div>
<div id="content" class="grid"><div class="loading">Загрузка консультаций…</div></div>
</main>
<script>
let token='',data=null,currentTab='attention',businessTimeZone='Europe/Moscow',businessTimeLabel='МСК';const pending=new Set();const content=document.getElementById('content'),metrics=document.getElementById('metrics'),message=document.getElementById('message'),freshness=document.getElementById('freshness'),lawyerName=document.getElementById('lawyerName'),filter=document.getElementById('filter'),match=document.getElementById('match');
function esc(v){return String(v??'').replace(/[&<>\x22\x27]/g,c=>c==='&'?'&amp;':c==='<'?'&lt;':c==='>'?'&gt;':c.charCodeAt(0)===34?'&quot;':'&#39;')}
function localHref(v,fallback='/lawyer/workspace/ui'){const value=String(v||'');return value.startsWith('/')&&!value.startsWith('//')?value:fallback}
function dt(v){if(!v)return'ещё не выбраны';try{const rendered=new Intl.DateTimeFormat('ru-RU',{dateStyle:'medium',timeStyle:'short',timeZone:businessTimeZone}).format(new Date(v));return businessTimeLabel?rendered+' '+businessTimeLabel:rendered}catch{return String(v)}}
function feedback(text,state=''){message.textContent=text;message.className='message '+state}
async function api(path,opts={}){const response=await fetch(path,{...opts,credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});if(response.status===401||response.status===403){location.href='/login';throw new Error('Сессия истекла или недостаточно прав')}const body=await response.json().catch(()=>({}));if(!response.ok)throw new Error(body.detail||'Ошибка запроса');return body}
async function boot(){try{const response=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!response.ok){location.href='/login';return}const session=await response.json();if(!(session.roles||[session.role]).includes('lawyer'))throw new Error('Требуется роль юриста');token=session.api_token||'';await load(null,false)}catch(error){showError(error)}}
function showError(error){content.innerHTML=`<div class="error"><b>Не удалось загрузить консультации</b><p>${esc(error.message||error)}</p><div class="actions"><button onclick="load(null,false)">Повторить</button><a class="button secondary" href="/lawyer/workspace/ui">Ко всем делам</a></div></div>`;feedback(error.message||String(error),'bad')}
async function load(button=null,silent=false){if(button){button.disabled=true;button.setAttribute('aria-busy','true')}if(!silent)content.innerHTML='<div class="loading">Загрузка консультаций…</div>';try{data=await api('/lawyer/consultation-desk/data');businessTimeZone=data.business_timezone||businessTimeZone;businessTimeLabel=data.business_timezone_label??businessTimeLabel;lawyerName.textContent=data.lawyer?.name||'Консультации';freshness.textContent='Обновлено '+dt(data.generated_at);renderMetrics();render()}catch(error){if(silent){feedback('Автообновление не выполнено: '+error.message,'warn-text')}else{showError(error)}throw error}finally{if(button){button.disabled=false;button.removeAttribute('aria-busy')}}}
function renderMetrics(){const summary=data?.summary||{};metrics.innerHTML=`<div class="metric"><b>${summary.active||0}</b><span>активных записей</span></div><div class="metric"><b>${summary.today||0}</b><span>сегодня (${esc(businessTimeLabel||businessTimeZone)})</span></div><div class="metric ${summary.requires_outcome?'warn':''}"><b>${summary.requires_outcome||0}</b><span>нужно завершить</span></div><div class="metric ${summary.documents_on_review?'warn':''}"><b>${summary.documents_on_review||0}</b><span>материалов на проверке</span></div>`}
function showTab(tab,button){currentTab=tab;document.querySelectorAll('.tabs button').forEach(item=>item.classList.toggle('active',item===button));render()}
function tabItems(){const items=data?.consultations||[];if(currentTab==='today')return items.filter(item=>item.is_today);if(currentTab==='attention')return items.filter(item=>item.documents_on_review>0||['outcome_due','in_progress','lawyer_no_show','schedule_missing','status_review'].includes(item.state));return items}
function filtered(){const items=tabItems(),query=(filter.value||'').trim().toLowerCase();if(!query)return items;return items.filter(item=>[item.case_number,item.client_name,item.question,item.label].some(value=>String(value||'').toLowerCase().includes(query)))}
function render(){const base=tabItems(),items=filtered();match.textContent=filter.value.trim()?`Найдено: ${items.length} из ${base.length}`:`Показано: ${items.length}`;content.innerHTML=items.map(card).join('')||empty(base.length)}
function empty(baseCount){if(baseCount&&filter.value.trim())return `<div class="empty"><b>Ничего не найдено</b><p>Измените запрос или очистите поиск — записи из текущего раздела не потеряны.</p><div class="actions"><button onclick="filter.value='';render()">Очистить поиск</button></div></div>`;const title=currentTab==='today'?'На сегодня консультаций нет':currentTab==='attention'?'Сейчас нет консультаций, требующих действия':'Активных консультаций нет';return `<div class="empty"><b>${esc(title)}</b><p>Это нормальное состояние очереди. Можно перейти к делам или переписке.</p><div class="actions"><a class="button" href="/lawyer/workspace/ui">Открыть все дела</a><a class="button secondary" href="/message-center/ui">Открыть сообщения</a></div></div>`}
function controls(id){return Array.from(document.querySelectorAll(`[data-consultation-id="${id}"]`))}
async function withAction(id,button,work){if(pending.has(id))return;pending.add(id);const items=controls(id),labels=new Map(items.filter(item=>item.tagName==='BUTTON').map(item=>[item,item.textContent]));items.forEach(item=>{item.disabled=true;item.setAttribute('aria-busy','true')});if(button)button.textContent='Сохранение…';try{return await work()}finally{pending.delete(id);items.forEach(item=>{item.disabled=false;item.removeAttribute('aria-busy')});labels.forEach((label,item)=>item.textContent=label)}}
function checklist(items){return (items||[]).map(item=>`<div class="check ${esc(item.state)}"><b>${item.state==='ready'?'✓ ':item.state==='pending'?'! ':'• '}${esc(item.label)}</b><small>${esc(item.detail)}</small></div>`).join('')}
function documents(x){if(!x.documents_count)return '';const rows=(x.documents||[]).map(item=>`<div class="document"><div><b>${esc(item.title)}</b><small>Версия ${esc(item.version)} · ${esc(item.document_type)}</small></div><span>${esc(item.status_label)}</span></div>`).join('');return `<details class="documents"><summary>Материалы клиента: ${esc(x.documents_count)}</summary>${rows}<div class="actions" style="margin-top:8px"><a class="button secondary" href="${esc(localHref(x.document_review_url))}">Открыть проверку документов</a></div></details>`}
function nextStep(x){if(x.can_complete)return {kind:'action',text:x.requires_outcome?'Зафиксируйте итоговое решение по завершённой встрече.':'После разговора зафиксируйте результат, чтобы клиент получил следующий шаг.',button:`<button class="green" data-consultation-id="${x.consultation_id}" onclick="openForm(${x.consultation_id},'complete')">Зафиксировать результат</button>`};if(x.state==='lawyer_no_show')return {kind:'attention',text:'Свяжитесь с клиентом. Перенос или возврат оформляет администратор.',button:`<a class="button" data-consultation-id="${x.consultation_id}" href="${esc(localHref(x.message_url))}">Написать клиенту</a>`};if(x.state==='schedule_missing'||x.state==='status_review')return {kind:'attention',text:'Не меняйте результат консультации. Откройте дело и восстановите актуальное состояние записи.',button:`<a class="button" data-consultation-id="${x.consultation_id}" href="${esc(localHref(x.case_url))}">Открыть дело</a>`};if(x.state==='client_no_show')return {kind:'attention',text:'Неявка уже зафиксирована. Дальнейшее решение выполняется через администратора; при необходимости сообщите клиенту.',button:`<a class="button" data-consultation-id="${x.consultation_id}" href="${esc(localHref(x.message_url))}">Открыть переписку</a>`};if(x.documents_on_review>0)return {kind:'action',text:'До встречи проверьте материалы клиента, которые уже поступили на проверку.',button:`<a class="button" data-consultation-id="${x.consultation_id}" href="${esc(localHref(x.document_review_url))}">Проверить материалы</a>`};if(x.state==='upcoming')return {kind:'',text:'Подготовка готова. Дождитесь времени встречи; дополнительных изменений сейчас не требуется.',button:''};if(['question_pending','materials_pending','slot_pending','confirmation_pending'].includes(x.state))return {kind:'',text:'Сейчас следующий шаг выполняет клиент. Не меняйте статус вручную; при необходимости помогите через переписку.',button:''};return {kind:'',text:'Откройте актуальное дело, если требуется уточнить следующий шаг.',button:`<a class="button secondary" data-consultation-id="${x.consultation_id}" href="${esc(localHref(x.case_url))}">Открыть дело</a>`}}
function card(x){const next=nextStep(x);const secondary=[`<a class="button ghost" data-consultation-id="${x.consultation_id}" href="${esc(localHref(x.case_url))}">Дело</a>`,`<a class="button ghost" data-consultation-id="${x.consultation_id}" href="${esc(localHref(x.message_url))}">Переписка</a>`];if(x.documents_count)secondary.push(`<a class="button ghost" data-consultation-id="${x.consultation_id}" href="${esc(localHref(x.document_review_url))}">Документы</a>`);const exception=x.can_mark_no_show?`<details class="exception"><summary>Исключение: клиент не подключился</summary><div class="muted" style="margin-top:6px">Используйте только если прошло не менее 15 минут после начала и клиент действительно отсутствует. Нормальное завершение встречи оформляется выше через результат.</div><button class="danger" data-consultation-id="${x.consultation_id}" onclick="openForm(${x.consultation_id},'no_show')">Зафиксировать неявку</button></details>`:'';return `<article class="card ${esc(x.state)}" id="consultation_${x.consultation_id}"><div class="card-head"><div><h3>${esc(x.case_number)}</h3><div>${esc(x.client_name)}</div></div><span class="badge ${esc(x.state)}">${esc(x.label)}</span></div><div class="section-label">Сейчас</div><div class="now-box"><b>${dt(x.scheduled_at)}</b><span>${esc(x.detail)}</span>${x.ends_at?`<div class="muted">Окончание слота: ${dt(x.ends_at)}</div>`:''}</div><div class="section-label">Главный следующий шаг</div><div class="next-box ${esc(next.kind)}"><b>${esc(next.text)}</b>${next.button?`<div class="actions" style="margin-top:9px">${next.button}</div>`:''}</div><div class="section-label">Подготовка</div><div class="question"><span>Вопрос клиента</span>${esc(x.question||'Описание ещё не сохранено')}</div><div class="checklist">${checklist(x.checklist)}</div>${documents(x)}<div class="secondary-actions">${secondary.join('')}</div>${exception}<div class="form" id="form_${x.consultation_id}"><h4 id="form_title_${x.consultation_id}"></h4><div id="form_help_${x.consultation_id}" class="form-help"></div><textarea id="result_${x.consultation_id}" placeholder="Опишите итог разговора или причину неявки"></textarea><select id="decision_${x.consultation_id}"><option value="">Выберите итоговое решение</option><option value="close">Закрыть обращение</option><option value="to_m1">Перевести в маршрут М1</option><option value="follow_up">Нужна следующая консультация</option><option value="other">Иное решение</option></select><div class="actions"><button data-consultation-id="${x.consultation_id}" onclick="submitResult(${x.consultation_id},this)">Подтвердить</button><button class="secondary" data-consultation-id="${x.consultation_id}" onclick="closeForm(${x.consultation_id})">Вернуться без сохранения</button></div></div></article>`}
function openForm(id,type){document.querySelectorAll('.form').forEach(form=>form.classList.remove('open'));const form=document.getElementById('form_'+id);form.dataset.type=type;document.getElementById('form_title_'+id).textContent=type==='complete'?'Зафиксировать результат консультации':'Зафиксировать неявку клиента';document.getElementById('form_help_'+id).textContent=type==='complete'?'Опишите вывод для клиента и выберите дальнейший маршрут. Изменение произойдёт только после подтверждения.':'Укажите фактическую причину. Используйте этот путь только для подтверждённой неявки после допустимого времени.';document.getElementById('result_'+id).value='';const decision=document.getElementById('decision_'+id);decision.value='';decision.style.display=type==='complete'?'block':'none';form.classList.add('open');document.getElementById('result_'+id).focus();form.scrollIntoView({behavior:'smooth',block:'nearest'})}
function closeForm(id){document.getElementById('form_'+id).classList.remove('open')}
async function submitResult(id,button){const form=document.getElementById('form_'+id),type=form.dataset.type,result=document.getElementById('result_'+id).value.trim(),decision=document.getElementById('decision_'+id).value;const minimum=type==='complete'?20:5;if(result.length<minimum){feedback(`Опишите ${type==='complete'?'результат':'причину'} — минимум ${minimum} символов.`,'bad');return}if(type==='complete'&&!decision){feedback('Выберите итоговое решение.','bad');return}if(!confirm(type==='complete'?'Зафиксировать результат консультации и передать клиенту следующий шаг?':'Зафиксировать неявку клиента?'))return;const path=type==='complete'?`/lawyer/consultations/${id}/complete`:`/lawyer/consultations/${id}/client-no-show`;const body=type==='complete'?{result,decision}:{comment:result};return withAction(id,button,async()=>{try{await api(path,{method:'POST',body:JSON.stringify(body)})}catch(error){feedback('Операция не выполнена: '+error.message,'bad');return}try{await load(null,true)}catch(error){feedback((type==='complete'?'Результат сохранён, но список не обновился: ':'Неявка сохранена, но список не обновился: ')+error.message,'warn-text');return}feedback(type==='complete'?'Результат сохранён; клиент получит актуальный следующий шаг.':'Неявка сохранена; дальнейшее решение доступно администратору.','ok')})}
setInterval(()=>{if(document.visibilityState==='visible'&&!pending.size){void load(null,true).catch(()=>{})}},60000);
boot();
</script>
</body>
</html>
"""
