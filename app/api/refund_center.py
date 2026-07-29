from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.domain.payments.refund_service import ConsultationRefundService
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
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f4f5f7;margin:0;color:#111827}
header{background:#111827;color:#fff;padding:18px 24px;display:flex;justify-content:space-between;align-items:center}
main{max-width:1200px;margin:auto;padding:24px}.card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:16px}
table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}
button{border:0;border-radius:9px;padding:9px 12px;color:#fff;font-weight:700;cursor:pointer;background:#2563eb}.green{background:#15803d}.red{background:#b91c1c}
.muted{color:#6b7280;font-size:13px}.notice{background:#fffbeb;border:1px solid #fde68a;border-radius:12px;padding:12px;margin-bottom:16px}
@media(max-width:800px){table{font-size:12px}.actions button{display:block;width:100%;margin:4px 0}}
</style>
</head>
<body>
<header><b>⚖ Возвраты консультаций</b><a href="/admin-ui" style="color:white">Админка</a></header>
<main>
<div class="notice"><b>Важно.</b> Эта панель не отправляет деньги через платежного провайдера. Сначала выполните фактический возврат в кабинете провайдера, затем нажмите «Возврат выполнен» для фиксации результата в системе.</div>
<div class="card"><h2>Ожидают решения</h2><div id="content">Загрузка…</div></div>
<div id="message" class="muted"></div>
</main>
<script>
let token='';
async function api(path,opts={}){const r=await fetch(path,{...opts,headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка');return d}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function boot(){const r=await fetch('/auth/session');if(!r.ok){location.href='/login';return}const s=await r.json();token=s.api_token;await load()}
async function load(){const rows=await api('/admin/refunds');content.innerHTML=rows.length?`<table><thead><tr><th>Платёж</th><th>Дело / клиент</th><th>Сумма</th><th>Провайдер</th><th>Действия</th></tr></thead><tbody>${rows.map(x=>`<tr><td>#${x.payment_id}<br><span class="muted">${esc(x.status)}</span></td><td><b>${esc(x.case_number)}</b><br>${esc(x.client_name||'—')}<br><span class="muted">TG ${esc(x.telegram_id)}</span></td><td>${x.amount.toLocaleString('ru-RU')} ${esc(x.currency)}</td><td>${esc(x.provider||'—')}<br><span class="muted">${esc(x.provider_payment_id||'')}</span></td><td class="actions"><button class="green" onclick="resolveRefund(${x.payment_id},'refunded')">Возврат выполнен</button> <button class="red" onclick="resolveRefund(${x.payment_id},'declined')">Отказать</button></td></tr>`).join('')}</tbody></table>`:'Заявок на возврат нет.'}
async function resolveRefund(id,decision){const comment=prompt(decision==='refunded'?'Укажите номер операции возврата или комментарий:':'Укажите причину отказа:');if(!comment)return;try{await api('/admin/refunds/'+id+'/resolve',{method:'POST',body:JSON.stringify({decision,comment})});message.textContent='Решение сохранено';await load()}catch(e){message.textContent=e.message}}
boot();
</script>
</body>
</html>
"""
