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
            "case_detail_url": f"/admin/workdesk/ui?case_id={case.id}",
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
        response = {
            "ok": True,
            "payment_id": int(payment.id),
            "case_id": int(payment.case_id),
            "status": str(payment.status),
        }
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

    return response


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
:root{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--green:#14804a;--green-soft:#ecfdf3;--red:#b42318;--red-soft:#fef3f2;--amber:#a15c00;--amber-soft:#fff7e6;--blue:#3157d5;--blue-soft:#eef2ff}*{box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:var(--bg);margin:0;color:var(--ink)}header{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:18px 24px}header .inner{max-width:1200px;margin:auto;display:flex;justify-content:space-between;align-items:center;gap:12px}header .links{display:flex;gap:10px;flex-wrap:wrap}header a{color:#fff}header .sub{font-size:12px;color:#d0d5dd;margin-top:4px}main{max-width:1200px;margin:auto;padding:22px}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:16px}.section-label{font-size:11px;font-weight:850;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:7px}.summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px}.metric{background:#fff;border:1px solid var(--line);border-radius:12px;padding:11px}.metric b{display:block;font-size:23px}.next{margin-top:12px;padding:12px;background:var(--blue-soft);border:1px solid #c7d7fe;border-radius:12px;line-height:1.45}.secondary-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}button,.button{border:0;border-radius:9px;padding:9px 12px;color:#fff;font-weight:700;cursor:pointer;background:var(--blue);text-decoration:none;display:inline-block}button:disabled{opacity:.55;cursor:wait}.green{background:var(--green)}.red{background:var(--red)}.gray{background:#475467}.muted{color:var(--muted);font-size:13px}.ok{color:var(--green)}.bad{color:var(--red)}.warn{color:var(--amber)}.notice{background:var(--amber-soft);border:1px solid #fedf89;border-radius:12px;padding:12px;margin-bottom:16px}.context{display:inline-flex;border-radius:999px;padding:5px 9px;font-size:12px;font-weight:800}.context.keep{background:var(--green-soft);color:var(--green)}.context.other{background:#eef2f6;color:#475467}.empty{text-align:center;color:var(--muted);padding:24px}.empty .button{margin:12px 4px 0}.actions{display:grid;gap:6px;min-width:170px}.row{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}@media(max-width:900px){table{display:block;overflow-x:auto;font-size:12px}.summary{grid-template-columns:1fr 1fr}}@media(max-width:620px){header .inner,.row{align-items:flex-start;flex-direction:column}.summary{grid-template-columns:1fr}.secondary-actions>*{width:100%;text-align:center}}
</style>
</head>
<body>
<header><div class="inner"><div><b id="pageTitle">⚖ Возвраты консультаций</b><div class="sub" id="staffContext">Роль: администратор / суперадминистратор · время загружается…</div></div><div class="links"><a href="/admin/workdesk/ui">Рабочий стол</a><a href="/admin/refunds/ui">Вся очередь</a></div></div></header>
<main>
<section class="card"><div class="section-label">Сейчас</div><div id="refundSummary" class="summary"><div class="metric"><b>—</b><span class="muted">загрузка очереди</span></div></div><div id="refundNext" class="next"><b>Главный следующий шаг</b><div class="muted">Проверяем возвраты и их фактический статус у провайдера.</div></div><div class="section-label" style="margin-top:14px">Вторичные действия</div><div class="secondary-actions"><button class="gray" onclick="load()">Обновить очередь</button><a class="button gray" href="/admin/workdesk/ui">Рабочий стол</a></div></section>
<div class="notice"><b>Важно.</b> Эта панель не отправляет деньги через платёжного провайдера. Сначала выполните фактический возврат в кабинете провайдера, затем зафиксируйте результат здесь. Если у дела уже есть подтверждённая консультация, финансовый возврат лишнего платежа не отменяет её автоматически.</div>
<div class="card"><div class="section-label">Рабочая очередь</div><h2 id="sectionTitle">Ожидают решения</h2><div id="content">Загрузка…</div></div>
<div id="message" class="muted" role="status" aria-live="polite"></div>
</main>
<script>
const params=new URLSearchParams(location.search),requestedPaymentId=Number(params.get('payment_id')||0),requestedCaseId=Number(params.get('case_id')||0);let token='',terminalCaseId=requestedCaseId||0,businessTimeZone='Europe/Moscow',businessTimeLabel='МСК';const pendingPayments=new Set(),refundDrafts=new Map();
async function api(path,opts={}){const r=await fetch(path,{...opts,credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});if(r.status===401||r.status===403){location.href='/login';const e=new Error('Сессия истекла или недостаточно прав');e.status=r.status;throw e}const d=await r.json().catch(()=>({}));if(!r.ok){const e=new Error(d.detail||'Ошибка');e.status=r.status;e.detail=d.detail||'';throw e}return d}
function feedback(text,state='ok'){message.textContent=text;message.className='muted '+state}
function paymentButtons(id){return Array.from(document.querySelectorAll(`[data-payment-id="${id}"]`))}
async function withPaymentAction(id,button,work){if(pendingPayments.has(id))return;pendingPayments.add(id);const buttons=paymentButtons(id);const labels=new Map(buttons.map(x=>[x,x.textContent]));buttons.forEach(x=>{x.disabled=true;x.setAttribute('aria-busy','true')});if(button)button.textContent='Выполняется…';try{return await work()}finally{pendingPayments.delete(id);buttons.forEach(x=>{x.disabled=false;x.removeAttribute('aria-busy')});labels.forEach((label,x)=>{x.textContent=label})}}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function formatDate(v){if(!v)return '—';try{const rendered=new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short',timeZone:businessTimeZone}).format(new Date(v));return businessTimeLabel?rendered+' '+businessTimeLabel:rendered}catch{return String(v)}}
function visibleRows(rows){return requestedPaymentId?rows.filter(x=>Number(x.payment_id)===requestedPaymentId):rows}
function updateRefundSummary(rows){const pending=rows.filter(x=>!x.retry_only).length,retry=rows.filter(x=>x.retry_only).length,preserved=rows.filter(x=>x.active_booking_preserved).length;refundSummary.innerHTML=`<div class="metric"><b>${pending}</b><span class="muted">ожидают фиксации результата</span></div><div class="metric"><b>${retry}</b><span class="muted">требуют повторной попытки</span></div><div class="metric"><b>${preserved}</b><span class="muted">записей консультации сохраняются</span></div>`;if(retry>0)refundNext.innerHTML='<b>Главный следующий шаг</b><div class="warn">Сначала разберите возвраты с отказом: устраните причину у провайдера и только затем верните платёж в рабочую очередь. Кнопка повтора сама деньги не отправляет.</div>';else if(pending>0)refundNext.innerHTML='<b>Главный следующий шаг</b><div>Сверьте фактическую операцию у платёжного провайдера для самого раннего ожидающего возврата, затем отдельно зафиксируйте «Возврат выполнен» или обоснованный отказ.</div>';else refundNext.innerHTML='<b>Главный следующий шаг</b><div class="ok">Возвратов, требующих действия, сейчас нет.</div>'}
function terminalState(){if(!requestedPaymentId)return '<div class="empty">Заявок на возврат нет.</div>';const back=terminalCaseId?`<a class="button" href="/admin/workdesk/ui?case_id=${terminalCaseId}">Вернуться в дело</a>`:'';return `<div class="empty"><b>Платёж #${requestedPaymentId} больше не требует обработки возврата.</b><br><span>Решение уже сохранено, статус изменился или платёж больше не находится в этой очереди.</span><br>${back}<a class="button gray" href="/admin/refunds/ui">Открыть всю очередь</a></div>`}
function renderRows(rows){if(!rows.length){content.innerHTML=terminalState();return}content.innerHTML=`<table><thead><tr><th>Платёж</th><th>Дело / клиент</th><th>Сумма</th><th>Контекст дела</th><th>Провайдер</th><th>Действия</th></tr></thead><tbody>${rows.map(x=>`<tr><td>#${x.payment_id}<br><span class="muted">${esc(x.status)}</span>${x.requested_at?`<br><span class="muted">в очереди с ${esc(formatDate(x.requested_at))}</span>`:''}</td><td><b>${esc(x.case_number)}</b><br>${esc(x.client_name||'—')}<br><span class="muted">TG ${esc(x.telegram_id)}</span></td><td>${x.amount.toLocaleString('ru-RU')} ${esc(x.currency)}</td><td>${x.active_booking_preserved?'<span class="context keep">Запись сохранена</span>':'<span class="context other">Запись не подтверждена</span>'}<div class="muted">Статус дела: ${esc(x.case_status||'—')}</div><a href="${esc(x.case_detail_url)}">Открыть дело</a></td><td>${esc(x.provider||'—')}<br><span class="muted">${esc(x.provider_payment_id||'')}</span></td><td class="actions"><button data-payment-id="${x.payment_id}" class="green" onclick="resolveRefund(${x.payment_id},'refunded',this)">Возврат выполнен</button><button data-payment-id="${x.payment_id}" class="red" onclick="resolveRefund(${x.payment_id},'declined',this)">Отказать</button></td></tr>`).join('')}</tbody></table>`}
async function boot(){const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();const roles=s.roles||[s.role];if(!(roles.includes('admin')||roles.includes('superadmin'))){location.href='/operator';return}token=s.api_token;businessTimeZone=s.business_timezone||businessTimeZone;businessTimeLabel=s.business_timezone_label??businessTimeLabel;staffContext.textContent=`Роль: администратор / суперадминистратор · время: ${businessTimeLabel||businessTimeZone}`;if(requestedPaymentId){pageTitle.textContent=`⚖ Возврат по платежу #${requestedPaymentId}`;sectionTitle.textContent='Конкретный возврат'}try{await load()}catch(e){feedback(e.message,'bad');refundNext.innerHTML='<b>Главный следующий шаг</b><div class="bad">Не фиксируйте возврат вслепую. Повторите загрузку очереди или вернитесь в рабочий стол.</div>'}}
async function load(){const rows=await api('/admin/refunds');const visible=visibleRows(rows);if(requestedPaymentId&&visible.length){terminalCaseId=Number(visible[0].case_id)||terminalCaseId}updateRefundSummary(visible);renderRows(visible)}
async function resolveRefund(id,decision,button){const previous=refundDrafts.get(Number(id)),defaultComment=previous&&previous.decision===decision?previous.comment:'';const question=decision==='refunded'?'Укажите номер операции возврата или комментарий:':'Укажите причину отказа:';const entered=prompt(question,defaultComment);if(entered===null)return;const comment=entered.trim();if(comment.length<5){feedback('Комментарий должен содержать не менее 5 символов','bad');return}refundDrafts.set(Number(id),{decision,comment});const warning=decision==='refunded'?`Подтвердите, что деньги по платежу #${id} уже фактически возвращены через платёжного провайдера. Эта кнопка только фиксирует результат в системе.`:`Подтвердите отказ в возврате по платежу #${id}. Причина будет сохранена в истории дела.`;if(!confirm(warning))return;return withPaymentAction(id,button,async()=>{try{const result=await api('/admin/refunds/'+id+'/resolve',{method:'POST',body:JSON.stringify({decision,comment})});refundDrafts.delete(Number(id));terminalCaseId=Number(result.case_id)||terminalCaseId;feedback(`Решение по платежу #${result.payment_id} сохранено: ${result.status}`,'ok');try{await load()}catch(e){feedback(`Решение сохранено, но список не обновился: ${e.message}`,'warn')}}catch(e){if(e.status===409){try{await load()}catch(refreshError){feedback(`Карточка платежа #${id} устарела, решение не применено. Не удалось обновить очередь: ${refreshError.message}. Ваш комментарий сохранён в этой вкладке.`,'bad');return}feedback(`Карточка платежа #${id} устарела, решение не применено. Очередь обновлена; сверьте актуальный статус. Ваш комментарий сохранён в этой вкладке.`,'warn');return}feedback(`Решение по платежу #${id} не сохранено: ${e.message}`,'bad')}})}
boot();
</script>
</body>
</html>
"""