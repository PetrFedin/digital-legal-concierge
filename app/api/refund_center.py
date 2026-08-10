from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.domain.payments.refund_service import ConsultationRefundService
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.payment import Payment
from app.models.user import User
from app.security.access_control import ROLE_ADMIN, decode_access_token, has_role

router = APIRouter(prefix="/admin/refunds", tags=["admin", "refunds"])


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


@router.get("")
async def list_refund_requests(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    rows = (
        await db.execute(
            select(Payment, Case, User)
            .join(Case, Case.id == Payment.case_id)
            .join(User, User.id == Case.client_id)
            .where(Payment.status == PaymentStatus.REFUND_PENDING)
            .order_by(Payment.updated_at.asc(), Payment.id.asc())
        )
    ).all()
    return [
        {
            "payment_id": payment.id,
            "case_id": case.id,
            "case_number": case.case_number,
            "case_status": case.status,
            "active_booking_preserved": (
                str(case.status) == str(CaseStatus.M2_CONSULTATION_BOOKED)
            ),
            "case_detail_url": f"/admin/cases/{case.id}/ui",
            "client_id": user.id,
            "client_name": user.full_name,
            "telegram_id": user.telegram_id,
            "amount": float(payment.amount),
            "currency": payment.currency,
            "provider": payment.provider,
            "provider_payment_id": payment.provider_payment_id,
            "status": payment.status,
            "requested_at": (
                payment.updated_at.isoformat() if payment.updated_at else None
            ),
        }
        for payment, case, user in rows
    ]


@router.post("/{payment_id}/resolve")
async def resolve_refund(
    payment_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = require_admin(x_admin_token)
    try:
        payment = await ConsultationRefundService(db).resolve_refund(
            payment_id=payment_id,
            decision=payload.get("decision"),
            actor_id=actor_id_from_token(actor),
            comment=payload.get("comment") or "",
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
        "payment_id": payment.id,
        "status": payment.status,
    }


@router.get("/ui", response_class=HTMLResponse)
async def refund_center_ui():
    return HTMLResponse(REFUND_CENTER_HTML)


REFUND_CENTER_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Возвраты консультаций</title>
<style>
:root{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--green:#14804a;--green-soft:#ecfdf3;--red:#b42318;--red-soft:#fef3f2;--amber:#a15c00;--amber-soft:#fff7e6;--blue:#3157d5}*{box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:var(--bg);margin:0;color:var(--ink)}header{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:18px 24px}header .inner{max-width:1200px;margin:auto;display:flex;justify-content:space-between;align-items:center;gap:12px}header .links{display:flex;gap:10px}header a{color:#fff}main{max-width:1200px;margin:auto;padding:22px}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:16px}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}button,.button{border:0;border-radius:9px;padding:9px 12px;color:#fff;font-weight:700;cursor:pointer;background:var(--blue);text-decoration:none;display:inline-block}button:disabled{opacity:.55;cursor:wait}.green{background:var(--green)}.red{background:var(--red)}.gray{background:#475467}.muted{color:var(--muted);font-size:13px}.ok{color:var(--green)}.bad{color:var(--red)}.warn{color:var(--amber)}.notice{background:var(--amber-soft);border:1px solid #fedf89;border-radius:12px;padding:12px;margin-bottom:16px}.context{display:inline-flex;border-radius:999px;padding:5px 9px;font-size:12px;font-weight:800}.context.keep{background:var(--green-soft);color:var(--green)}.context.other{background:#eef2f6;color:#475467}.empty{text-align:center;color:var(--muted);padding:24px}.actions{display:grid;gap:6px;min-width:170px}@media(max-width:900px){table{display:block;overflow-x:auto;font-size:12px}}@media(max-width:620px){header .inner{align-items:flex-start;flex-direction:column}}
</style>
</head>
<body>
<header><div class="inner"><div><b>⚖ Возвраты консультаций</b><div style="font-size:12px;color:#d0d5dd">Фиксация результата после фактической операции у платёжного провайдера</div></div><div class="links"><a href="/operator">Рабочее пространство</a><a href="/admin-ui">Админка</a></div></div></header>
<main>
<div class="notice"><b>Важно.</b> Эта панель не отправляет деньги через платёжного провайдера. Сначала выполните фактический возврат в кабинете провайдера, затем зафиксируйте результат здесь. Если у дела уже есть подтверждённая консультация, финансовый возврат лишнего платежа не отменяет её автоматически.</div>
<div class="card"><h2>Ожидают решения</h2><div id="content">Загрузка…</div></div>
<div id="message" class="muted" role="status" aria-live="polite"></div>
</main>
<script>
let token='';const pendingPayments=new Set();
async function api(path,opts={}){const r=await fetch(path,{...opts,credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});if(r.status===401||r.status===403){location.href='/login';throw new Error('Сессия истекла или недостаточно прав')}const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка');return d}
function feedback(text,state='ok'){message.textContent=text;message.className='muted '+state}
function paymentButtons(id){return Array.from(document.querySelectorAll(`[data-payment-id="${id}"]`))}
async function withPaymentAction(id,button,work){if(pendingPayments.has(id))return;pendingPayments.add(id);const buttons=paymentButtons(id);const labels=new Map(buttons.map(x=>[x,x.textContent]));buttons.forEach(x=>{x.disabled=true;x.setAttribute('aria-busy','true')});if(button)button.textContent='Выполняется…';try{return await work()}finally{pendingPayments.delete(id);buttons.forEach(x=>{x.disabled=false;x.removeAttribute('aria-busy')});labels.forEach((label,x)=>{x.textContent=label})}}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function boot(){const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();token=s.api_token;try{await load()}catch(e){feedback(e.message,'bad')}}
async function load(){const rows=await api('/admin/refunds');content.innerHTML=rows.length?`<table><thead><tr><th>Платёж</th><th>Дело / клиент</th><th>Сумма</th><th>Контекст дела</th><th>Провайдер</th><th>Действия</th></tr></thead><tbody>${rows.map(x=>`<tr><td>#${x.payment_id}<br><span class="muted">${esc(x.status)}</span></td><td><b>${esc(x.case_number)}</b><br>${esc(x.client_name||'—')}<br><span class="muted">TG ${esc(x.telegram_id)}</span></td><td>${x.amount.toLocaleString('ru-RU')} ${esc(x.currency)}</td><td>${x.active_booking_preserved?'<span class="context keep">Запись сохранена</span>':'<span class="context other">Запись не подтверждена</span>'}<div class="muted">Статус дела: ${esc(x.case_status||'—')}</div><a href="${esc(x.case_detail_url)}">Открыть дело</a></td><td>${esc(x.provider||'—')}<br><span class="muted">${esc(x.provider_payment_id||'')}</span></td><td class="actions"><button data-payment-id="${x.payment_id}" class="green" onclick="resolveRefund(${x.payment_id},'refunded',this)">Возврат выполнен</button><button data-payment-id="${x.payment_id}" class="red" onclick="resolveRefund(${x.payment_id},'declined',this)">Отказать</button></td></tr>`).join('')}</tbody></table>`:'<div class="empty">Заявок на возврат нет.</div>'}
async function resolveRefund(id,decision,button){const question=decision==='refunded'?'Укажите номер операции возврата или комментарий:':'Укажите причину отказа:';const comment=prompt(question);if(!comment)return;if(comment.trim().length<5){feedback('Комментарий должен содержать не менее 5 символов','bad');return}const warning=decision==='refunded'?`Подтвердите, что деньги по платежу #${id} уже фактически возвращены через платёжного провайдера. Эта кнопка только фиксирует результат в системе.`:`Подтвердите отказ в возврате по платежу #${id}. Причина будет сохранена в истории дела.`;if(!confirm(warning))return;return withPaymentAction(id,button,async()=>{try{const result=await api('/admin/refunds/'+id+'/resolve',{method:'POST',body:JSON.stringify({decision,comment})});feedback(`Решение по платежу #${result.payment_id} сохранено: ${result.status}`,'ok');try{await load()}catch(e){feedback(`Решение сохранено, но список не обновился: ${e.message}`,'warn')}}catch(e){feedback(`Решение по платежу #${id} не сохранено: ${e.message}`,'bad')}})}
boot();
</script>
</body>
</html>
"""
