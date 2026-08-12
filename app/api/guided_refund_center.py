from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.payment import Payment
from app.models.user import User
from app.security.access_control import ROLE_ADMIN, decode_access_token, has_role

router = APIRouter(prefix="/admin/refunds", tags=["guided-refunds"])


def _require_admin(token: str | None) -> dict:
    payload = decode_access_token(token)
    if not payload or not has_role(payload.get("roles"), ROLE_ADMIN):
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return payload


def _payment_kind(payment: Payment) -> tuple[str, str]:
    if payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT:
        return "M2", "Оплата консультации"
    return "M1", payment.title or "Платёж M1"


@router.get("/context")
async def refund_context(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    _require_admin(x_admin_token)
    rows = (
        await db.execute(
            select(Payment, Case, User)
            .join(Case, Case.id == Payment.case_id)
            .join(User, User.id == Case.client_id)
            .where(Payment.status == PaymentStatus.REFUND_PENDING)
            .order_by(Payment.updated_at.asc(), Payment.id.asc())
        )
    ).all()
    result = []
    for payment, case, user in rows:
        route, kind = _payment_kind(payment)
        result.append(
            {
                "payment_id": payment.id,
                "payment_code": payment.payment_code,
                "payment_title": payment.title,
                "kind": kind,
                "route": route,
                "case_id": case.id,
                "case_number": case.case_number,
                "case_status": case.status,
                "case_route": case.route,
                "case_preserved": True,
                "active_booking_preserved": bool(
                    route == "M2"
                    and str(case.status) == CaseStatus.M2_CONSULTATION_BOOKED.value
                ),
                "client_name": user.full_name,
                "telegram_id": user.telegram_id,
                "amount": float(payment.amount),
                "currency": payment.currency,
                "provider": payment.provider,
                "provider_payment_id": payment.provider_payment_id,
                "requested_at": payment.updated_at.isoformat() if payment.updated_at else None,
                "case_detail_url": f"/admin/cases/{case.id}/ui",
            }
        )
    return result


REFUND_UI = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Возвраты платежей</title>
<style>
:root{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--blue2:#eef2ff;--green:#14804a;--green2:#ecfdf3;--red:#b42318;--red2:#fef3f2;--amber:#a15c00;--amber2:#fff7e6}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif}header{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:18px 22px;position:sticky;top:0;z-index:10}.head{max-width:1120px;margin:auto;display:flex;align-items:center;justify-content:space-between;gap:12px}.links{display:flex;gap:8px;flex-wrap:wrap}.links a{color:#fff;text-decoration:none;border:1px solid #ffffff40;border-radius:9px;padding:8px 10px}main{max-width:1120px;margin:auto;padding:20px}.intro,.card,.empty,.error{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;margin-bottom:14px}.intro{background:var(--amber2);border-color:#fedf89}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.card{box-shadow:0 8px 24px rgba(16,24,40,.06)}.row{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}.badge{display:inline-flex;border-radius:999px;padding:5px 9px;font-size:12px;font-weight:800;background:var(--blue2);color:#2445b5}.badge.m1{background:var(--amber2);color:var(--amber)}.badge.m2{background:var(--blue2);color:#2445b5}.cell{background:#f8fafc;border-radius:10px;padding:10px}.cell span{display:block;color:var(--muted);font-size:12px}.muted{color:var(--muted);font-size:13px}.preserve{background:var(--green2);color:var(--green);border-radius:10px;padding:10px;margin:10px 0}.warning{background:var(--red2);color:var(--red);border-radius:10px;padding:10px;margin:10px 0}button,.button{border:0;border-radius:9px;padding:9px 12px;font-weight:750;color:#fff;background:var(--blue);cursor:pointer;text-decoration:none;display:inline-block}button.green{background:var(--green)}button.red{background:var(--red)}button.gray,.button.gray{background:#475467}button:disabled{opacity:.55;cursor:wait}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.empty,.error{text-align:center}.error{background:var(--red2);color:var(--red)}@media(max-width:720px){header{position:static}.head{align-items:flex-start;flex-direction:column}.grid{grid-template-columns:1fr}main{padding:12px}}
</style></head><body>
<header><div class="head"><div><b>↩️ Возвраты платежей</b><div style="font-size:12px;color:#d0d5dd">Сначала фактическая операция у провайдера → затем фиксация результата</div></div><div class="links"><a href="/admin/workdesk/ui">Рабочий стол</a><a href="/admin/refunds/ui">Вся очередь</a><a href="/operator">Все разделы</a></div></div></header>
<main><div class="intro"><b>Деньги эта панель не отправляет.</b> Выполните реальный возврат у платёжного провайдера, затем зафиксируйте результат. Юридический этап M1 и действующая запись M2 не меняются автоматически из-за финансового возврата.</div><div id="content">Загрузка…</div><div id="feedback" class="muted" role="status"></div></main>
<script>
const params=new URLSearchParams(location.search),wanted=Number(params.get('payment_id')||0);let token='';const busy=new Set(),content=document.getElementById('content'),feedback=document.getElementById('feedback');
function e(v){return String(v??'').replace(/[&<>\x22\x27]/g,c=>c==='&'?'&amp;':c==='<'?'&lt;':c==='>'?'&gt;':c.charCodeAt(0)===34?'&quot;':'&#39;')}function money(v,c){return Number(v).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2})+' '+e(c||'RUB')}function dt(v){if(!v)return'—';try{return new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short'}).format(new Date(v))}catch{return e(v)}}function say(t,s=''){feedback.textContent=t;feedback.className='muted '+s}
async function api(path,opt={}){const r=await fetch(path,{...opt,credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token,'Content-Type':'application/json',...(opt.headers||{})}});if(r.status===401||r.status===403){location.href='/login';throw Error('Сессия истекла')}const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.detail||'Ошибка запроса');return d}
function card(x){const m1=x.route==='M1',context=m1?'Устаревший M1-платёж':'Консультационный платёж M2',note=m1?'Текущий юридический этап сохранён. Этот платёж не будет повторно применять старый M1-переход.':x.active_booking_preserved?'Подтверждённая запись клиента сохранена; возврат лишнего платежа её не отменит.':'Статус консультации не будет изменён этой операцией.';return `<article class="card"><div class="row"><div><span class="badge ${m1?'m1':'m2'}">${context}</span><h3>${e(x.payment_title||x.kind)} · #${x.payment_id}</h3><div class="muted">${e(x.case_number)} · ${e(x.client_name||'—')} · получено ${dt(x.requested_at)}</div></div><b>${money(x.amount,x.currency)}</b></div><div class="grid"><div class="cell"><span>Текущий статус дела</span>${e(x.case_status)}</div><div class="cell"><span>Провайдер</span>${e(x.provider||'—')} ${x.provider_payment_id?'· '+e(x.provider_payment_id):''}</div></div><div class="preserve"><b>Что останется без изменений</b><br>${e(note)}</div><div class="warning"><b>Перед подтверждением:</b> сначала выполните реальный возврат у провайдера. Эта кнопка только фиксирует уже выполненный результат.</div><div class="actions"><button class="green" data-p="${x.payment_id}" onclick="resolve(${x.payment_id},'refunded',this)">Возврат выполнен</button><button class="red" data-p="${x.payment_id}" onclick="resolve(${x.payment_id},'declined',this)">Зафиксировать отказ</button><a class="button gray" href="${e(x.case_detail_url)}">Открыть дело</a></div></article>`}
function render(rows){const visible=wanted?rows.filter(x=>Number(x.payment_id)===wanted):rows;if(!visible.length){content.innerHTML=`<div class="empty"><b>${wanted?'Этот платёж больше не требует возврата':'Очередь возвратов пуста'}</b><p>${wanted?'Решение уже сохранено или статус платежа изменился.':'Новых финансовых действий нет.'}</p><a class="button gray" href="/admin/workdesk/ui">Рабочий стол</a></div>`;return}content.innerHTML=visible.map(card).join('')}
async function load(){render(await api('/admin/refunds/context'))}async function resolve(id,decision,button){if(busy.has(id))return;const question=decision==='refunded'?'Номер операции возврата / подтверждение провайдера:':'Причина отказа в возврате:';const comment=prompt(question);if(!comment)return;if(comment.trim().length<5){say('Комментарий должен содержать не менее 5 символов.','bad');return}const confirmText=decision==='refunded'?'Подтвердите: деньги уже фактически возвращены через платёжного провайдера?':'Подтвердите фиксацию отказа. Причина будет сохранена в истории.';if(!confirm(confirmText))return;busy.add(id);const old=button.textContent;button.disabled=true;button.textContent='Сохраняем…';try{const r=await api('/admin/refunds/'+id+'/resolve',{method:'POST',body:JSON.stringify({decision,comment})});say(`Решение по платежу #${r.payment_id} сохранено: ${r.status}`,'ok');await load()}catch(err){say('Решение не сохранено: '+err.message,'bad')}finally{busy.delete(id);button.disabled=false;button.textContent=old}}
async function boot(){const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();if(!(s.roles||[s.role]).includes('admin')){location.href='/operator';return}token=s.api_token||'';try{await load()}catch(err){content.innerHTML=`<div class="error"><b>Очередь не загружена</b><p>${e(err.message)}</p><button onclick="load().catch(x=>say(x.message,'bad'))">Повторить</button></div>`}}boot();
</script></body></html>
"""


@router.get("/ui", response_class=HTMLResponse)
async def guided_refund_ui(
    request: Request,
    x_admin_token: str | None = Header(default=None),
):
    token = x_admin_token or request.cookies.get(settings.admin_session_cookie)
    if not token:
        return RedirectResponse(url="/login", status_code=303)
    try:
        _require_admin(token)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(REFUND_UI)
