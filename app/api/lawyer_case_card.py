from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.cases.case_activity import CaseActivityService
from app.domain.cases.case_responsibility import lawyer_can_access_case
from app.domain.cases.case_timeline import get_client_visible_status
from app.domain.cases.service_modes import M1ServiceMode
from app.domain.statuses.document_statuses import DocumentStatus
from app.models.calculation import Calculation
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.message import Message
from app.models.payment import Payment
from app.models.self_filing_package import SelfFilingPackage
from app.models.user import User
from app.security.lawyer_access import require_lawyer_actor

router = APIRouter(tags=["lawyer-case-card"])


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


def _route_label(case: Case) -> str:
    if (
        str(case.route or "") == "M1"
        and str(case.service_mode or "") == M1ServiceMode.SELF_FILING_PACKAGE.value
    ):
        return "Пакет для самостоятельной подачи"
    return {"M1": "Ведение дела", "M2": "Консультация"}.get(
        str(case.route or ""),
        "Юридическое обращение",
    )


async def _authorized_case(
    db: AsyncSession,
    *,
    lawyer_id: int,
    case_id: int,
) -> tuple[Case, User]:
    row = (
        await db.execute(
            select(Case, User)
            .join(User, User.id == Case.client_id)
            .where(Case.id == int(case_id))
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Дело не найдено")
    case, user = row
    if not await lawyer_can_access_case(db, case=case, lawyer_id=lawyer_id):
        raise HTTPException(
            status_code=403,
            detail="Дело не относится к вашей текущей ответственности",
        )
    return case, user


@router.get("/lawyer/cases/{case_id}/workspace")
async def lawyer_case_workspace(
    case_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await require_lawyer_actor(db, _token(request, x_admin_token))
    case, user = await _authorized_case(
        db,
        lawyer_id=int(actor.lawyer.id),
        case_id=case_id,
    )

    documents = list(
        (
            await db.execute(
                select(Document)
                .where(Document.case_id == int(case.id))
                .order_by(
                    Document.document_type.asc(),
                    Document.version.desc(),
                    Document.id.desc(),
                )
            )
        ).scalars().all()
    )
    payments = list(
        (
            await db.execute(
                select(Payment)
                .where(Payment.case_id == int(case.id))
                .order_by(Payment.created_at.desc(), Payment.id.desc())
            )
        ).scalars().all()
    )
    calculation = (
        await db.execute(
            select(Calculation)
            .where(Calculation.case_id == int(case.id))
            .order_by(Calculation.created_at.desc(), Calculation.id.desc())
            .limit(1)
        )
    ).scalars().first()
    consultation = (
        await db.execute(
            select(Consultation)
            .where(Consultation.case_id == int(case.id))
            .order_by(Consultation.created_at.desc(), Consultation.id.desc())
            .limit(1)
        )
    ).scalars().first()
    messages = list(
        (
            await db.execute(
                select(Message)
                .where(Message.case_id == int(case.id))
                .order_by(Message.id.desc())
                .limit(10)
            )
        ).scalars().all()
    )
    self_filing = None
    if str(case.service_mode or "") == M1ServiceMode.SELF_FILING_PACKAGE.value:
        self_filing = (
            await db.execute(
                select(SelfFilingPackage).where(
                    SelfFilingPackage.case_id == int(case.id)
                )
            )
        ).scalar_one_or_none()
    activity = await CaseActivityService(db).page(
        case_id=int(case.id),
        audience="staff",
        limit=12,
    )

    return {
        "case": {
            "id": int(case.id),
            "number": case.case_number,
            "route": case.route,
            "service_mode": case.service_mode,
            "route_label": _route_label(case),
            "status": str(case.status),
            "status_label": get_client_visible_status(case.status),
            "next_action": case.next_action,
            "created_at": case.created_at.isoformat() if case.created_at else None,
            "updated_at": case.updated_at.isoformat() if case.updated_at else None,
            "sla_status": case.sla_status,
            "sla_due_at": case.sla_due_at.isoformat() if case.sla_due_at else None,
        },
        "client": {
            "id": int(user.id),
            "name": user.full_name,
            "phone": user.phone,
            "email": user.email,
            "telegram_id": int(user.telegram_id),
            "username": user.telegram_username,
        },
        "calculation": (
            {
                "id": int(calculation.id),
                "contract_price": str(calculation.contract_price)
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
                "delay_days": (
                    calculation.delay_days_chargeable
                    if calculation.delay_days_chargeable is not None
                    else calculation.delay_days
                ),
                "penalty_amount": str(calculation.penalty_amount)
                if calculation.penalty_amount is not None
                else None,
                "is_preliminary": bool(calculation.is_preliminary),
                "manual_review_required": calculation.manual_review_required,
                "manual_review_reasons": calculation.manual_review_reasons or [],
            }
            if calculation
            else None
        ),
        "documents": [
            {
                "id": int(item.id),
                "type": item.document_type,
                "title": item.title,
                "file_name": item.file_name,
                "status": str(item.status),
                "version": int(item.version or 1),
                "lawyer_comment": item.lawyer_comment,
                "archived": str(item.status) == DocumentStatus.ARCHIVED.value,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in documents
        ],
        "payments": [
            {
                "id": int(item.id),
                "code": str(item.payment_code),
                "title": item.title,
                "amount": str(item.amount),
                "currency": item.currency,
                "status": str(item.status),
                "paid_at": item.paid_at.isoformat() if item.paid_at else None,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in payments
        ],
        "consultation": (
            {
                "id": int(consultation.id),
                "status": str(consultation.status),
                "scheduled_at": (
                    consultation.scheduled_at.isoformat()
                    if consultation.scheduled_at
                    else None
                ),
                "result": consultation.result,
                "decision": consultation.decision,
            }
            if consultation
            else None
        ),
        "messages": [
            {
                "id": int(item.id),
                "sender_type": item.sender_type,
                "text": item.text,
                "is_read": bool(item.is_read),
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in messages
        ],
        "activity": activity,
        "self_filing": (
            {
                "status": self_filing.status,
                "version": int(self_filing.version or 1),
                "court_name": self_filing.court_name,
                "court_address": self_filing.court_address,
                "jurisdiction_basis": self_filing.jurisdiction_basis,
                "documents_complete_at": (
                    self_filing.documents_complete_at.isoformat()
                    if self_filing.documents_complete_at
                    else None
                ),
                "payment_confirmed_at": (
                    self_filing.payment_confirmed_at.isoformat()
                    if self_filing.payment_confirmed_at
                    else None
                ),
                "sla_due_at": (
                    self_filing.sla_due_at.isoformat()
                    if self_filing.sla_due_at
                    else None
                ),
                "email_delivery_status": self_filing.email_delivery_status,
            }
            if self_filing
            else None
        ),
        "business_timezone": settings.business_timezone,
        "business_timezone_label": settings.business_timezone_label,
    }


LAWYER_CASE_HTML = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Карточка дела юриста</title>
<style>
:root{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--green:#14804a;--amber:#a15c00;--shadow:0 12px 32px rgba(16,24,40,.07)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}header{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:18px 22px}.head,main{max-width:1180px;margin:auto}.head{display:flex;justify-content:space-between;gap:12px;align-items:center}.links,.actions{display:flex;gap:8px;flex-wrap:wrap}.button,button{display:inline-block;border:0;border-radius:10px;padding:9px 12px;background:var(--blue);color:#fff;text-decoration:none;font-weight:750;cursor:pointer}.secondary{background:#475467}main{padding:20px}.hero,.card{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:15px;margin-bottom:13px;box-shadow:var(--shadow)}.hero{display:flex;justify-content:space-between;gap:14px;align-items:flex-start}.hero h2{margin:0 0 5px}.badge{display:inline-flex;border-radius:999px;padding:6px 9px;background:#eef2ff;color:#2445b5;font-size:12px;font-weight:750}.muted{color:var(--muted);font-size:12px;line-height:1.45}.next{background:#eef2ff;border:1px solid #c7d2fe;border-radius:12px;padding:11px;margin-top:11px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}.card h3{margin:0 0 10px}.data{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.cell{background:#f8fafc;border-radius:10px;padding:9px;min-width:0}.cell span{display:block;color:var(--muted);font-size:11px;margin-bottom:3px}.item{border-top:1px solid var(--line);padding:9px 0}.item:first-of-type{border-top:0}.empty,.error,.loading{padding:26px;text-align:center;border:1px dashed var(--line);border-radius:13px;color:var(--muted)}.error{color:#b42318;background:#fef3f2}.warn{color:var(--amber)}@media(max-width:760px){.head,.hero{align-items:flex-start;flex-direction:column}.grid,.data{grid-template-columns:1fr}.links,.actions{width:100%}.button,button{flex:1;text-align:center}main{padding:12px}}
</style></head><body>
<header><div class="head"><div><h1 style="margin:0 0 4px;font-size:22px">⚖ Карточка дела</h1><div style="font-size:13px;color:#d0d5dd">Расчёт, документы, история, коммуникации и текущая юридическая работа</div></div><div class="links"><a class="button secondary" href="/lawyer/workspace/ui">Мои дела</a><a class="button secondary" href="/lawyer/consultation-desk/ui">Консультации</a></div></div></header>
<main id="main"><div class="loading">Загрузка карточки…</div></main>
<script>
const caseId=Number('__CASE_ID__');let zone='Europe/Moscow',zoneLabel='МСК';const main=document.getElementById('main');
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function dt(v){if(!v)return'—';try{const x=new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short',timeZone:zone}).format(new Date(v));return zoneLabel?x+' '+zoneLabel:x}catch{return String(v)}}
function money(v,c='RUB'){if(v===null||v===undefined||v==='')return'—';const n=Number(v);return Number.isFinite(n)?new Intl.NumberFormat('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2}).format(n)+' '+esc(c):esc(v)+' '+esc(c)}
async function api(path){const r=await fetch(path,{credentials:'same-origin',cache:'no-store'});if(r.status===401||r.status===403){const d=await r.json().catch(()=>({}));if(r.status===401)location.href='/login';throw new Error(d.detail||'Недостаточно прав')}const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка запроса');return d}
function calc(x){if(!x)return'<div class="empty">Расчёт по этому делу ещё не сохранён.</div>';return `<div class="data"><div class="cell"><span>Цена ДДУ</span>${money(x.contract_price)}</div><div class="cell"><span>Передача по ДДУ</span>${esc(x.planned_transfer_date||'—')}</div><div class="cell"><span>Фактическая передача</span>${esc(x.actual_transfer_date||'—')}</div><div class="cell"><span>Расчёт на дату</span>${esc(x.calculation_date||'—')}</div><div class="cell"><span>Дней просрочки</span>${esc(x.delay_days??'—')}</div><div class="cell"><span>Предварительная сумма</span>${money(x.penalty_amount)}</div></div><div class="muted" style="margin-top:8px">${x.is_preliminary?'Расчёт предварительный. Юридическое решение принимается после проверки документов.':'Зафиксированный расчёт.'}</div>`}
function docs(rows){if(!rows.length)return'<div class="empty">Документов нет.</div>';return rows.map(x=>`<div class="item"><b>${esc(x.title)} · v${esc(x.version)}</b><div class="muted">${esc(x.status)} · ${esc(x.file_name)}</div>${x.lawyer_comment?`<div style="margin-top:4px">${esc(x.lawyer_comment)}</div>`:''}</div>`).join('')}
function payments(rows){if(!rows.length)return'<div class="empty">Платежей нет.</div>';return rows.map(x=>`<div class="item"><b>${esc(x.title)}</b><div>${money(x.amount,x.currency)} · ${esc(x.status)}</div><div class="muted">${esc(dt(x.paid_at||x.created_at))}</div></div>`).join('')}
function activity(x){const rows=x?.items||[];if(!rows.length)return'<div class="empty">История событий пока пуста.</div>';return rows.map(v=>`<div class="item"><b>${esc(v.title)}</b><div class="muted">${esc(v.actor_label)} · ${esc(dt(v.occurred_at))}</div>${v.detail?`<div style="margin-top:4px">${esc(v.detail)}</div>`:''}</div>`).join('')}
function messages(rows){if(!rows.length)return'<div class="empty">Сообщений пока нет.</div>';return rows.map(x=>`<div class="item"><b>${x.sender_type==='client'?'Клиент':'Команда'}</b><div class="muted">${esc(dt(x.created_at))} · ${x.is_read?'прочитано':'не прочитано'}</div><div style="margin-top:4px">${esc(x.text)}</div></div>`).join('')}
function render(d){zone=d.business_timezone||zone;zoneLabel=d.business_timezone_label??zoneLabel;const c=d.case,u=d.client,s=d.self_filing,consult=d.consultation;const routeActions=c.service_mode==='SELF_FILING_PACKAGE'?'<a class="button" href="/self-filing/ui?case_id='+caseId+'">Пакет самостоятельной подачи</a>':c.route==='M2'?'<a class="button" href="/lawyer/consultation-desk/ui">Открыть консультации</a>':'<a class="button" href="/document-access/review/ui?case_id='+caseId+'">Проверить документы</a>';main.innerHTML=`<section class="hero"><div><h2>${esc(c.number)}</h2><div>${esc(u.name||'Клиент')} · ${esc(c.route_label)}</div><div class="muted">Создано ${esc(dt(c.created_at))} · обновлено ${esc(dt(c.updated_at))}</div><div class="next"><b>Главный следующий шаг</b><br>${esc(c.next_action||'Проверить актуальное состояние дела')}</div><div class="actions" style="margin-top:10px">${routeActions}<a class="button secondary" href="/message-center/ui?case_id=${caseId}">Переписка</a><a class="button secondary" href="/document-access/ui?case_id=${caseId}">Материалы</a></div></div><span class="badge">${esc(c.status_label)}</span></section><div class="grid"><section class="card"><h3>Клиент</h3><div class="data"><div class="cell"><span>ФИО</span>${esc(u.name||'—')}</div><div class="cell"><span>Телефон</span>${esc(u.phone||'—')}</div><div class="cell"><span>Email</span>${esc(u.email||'—')}</div><div class="cell"><span>Telegram</span>${esc(u.username?('@'+u.username):'username не указан')}</div></div></section><section class="card"><h3>Расчёт</h3>${calc(d.calculation)}</section><section class="card"><h3>Документы</h3>${docs(d.documents)}</section><section class="card"><h3>Оплаты</h3>${payments(d.payments)}</section><section class="card"><h3>История процесса</h3>${activity(d.activity)}</section><section class="card"><h3>Коммуникации</h3>${messages(d.messages)}<div class="actions" style="margin-top:10px"><a class="button secondary" href="/message-center/ui?case_id=${caseId}">Вся переписка</a></div></section>${consult?`<section class="card"><h3>Консультация</h3><div class="data"><div class="cell"><span>Статус</span>${esc(consult.status)}</div><div class="cell"><span>Время</span>${esc(dt(consult.scheduled_at))}</div><div class="cell"><span>Решение</span>${esc(consult.decision||'—')}</div><div class="cell"><span>Результат</span>${esc(consult.result||'—')}</div></div></section>`:''}${s?`<section class="card"><h3>Пакет самостоятельной подачи</h3><div class="data"><div class="cell"><span>Статус</span>${esc(s.status)}</div><div class="cell"><span>Суд</span>${esc(s.court_name||'—')}</div><div class="cell"><span>Полный комплект</span>${esc(dt(s.documents_complete_at))}</div><div class="cell"><span>Выдать до</span>${esc(dt(s.sla_due_at))}</div><div class="cell"><span>Email-доставка</span>${esc(s.email_delivery_status)}</div><div class="cell"><span>Оплата подтверждена</span>${esc(dt(s.payment_confirmed_at))}</div></div></section>`:''}</div>`}
async function load(){if(!caseId){main.innerHTML='<div class="error">Не указан номер дела.</div>';return}try{render(await api('/lawyer/cases/'+caseId+'/workspace'))}catch(e){main.innerHTML=`<div class="error"><b>Карточка не загружена</b><p>${esc(e.message||e)}</p><button onclick="load()">Повторить</button> <a class="button secondary" href="/lawyer/workspace/ui">Мои дела</a></div>`}}
load();
</script></body></html>
"""


@router.get("/lawyer/cases/{case_id}/ui", response_class=HTMLResponse)
async def lawyer_case_ui(
    case_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    token = _token(request, x_admin_token)
    if not token:
        return RedirectResponse(
            url=f"/login?next=/lawyer/cases/{int(case_id)}/ui",
            status_code=303,
        )
    actor = await require_lawyer_actor(db, token)
    case, _user = await _authorized_case(
        db,
        lawyer_id=int(actor.lawyer.id),
        case_id=case_id,
    )
    return HTMLResponse(
        LAWYER_CASE_HTML.replace("__CASE_ID__", str(int(case.id))),
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router"]
