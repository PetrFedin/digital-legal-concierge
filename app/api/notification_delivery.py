from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.case_detail_page import CASE_DETAIL_HTML
from app.admin.notification_delivery import (
    NotificationDeliveryError,
    NotificationDeliveryService,
    serialize_notification,
)
from app.api.admin import actor_id_from_token, require_admin
from app.config import settings
from app.db.session import get_db
from app.domain.notifications.notification_sender import NotificationSender
from app.models.audit_log import AuditLog
from app.models.notification import Notification

router = APIRouter(prefix="/admin/notification-delivery", tags=["notification-delivery"])


async def _attempt_delivery(
    db: AsyncSession,
    notification_ids: tuple[int, ...],
) -> dict[str, object]:
    if not notification_ids:
        return {
            "status": "nothing_due",
            "requested": 0,
            "processed": 0,
            "sent": 0,
            "retry": 0,
            "failed": 0,
        }
    try:
        summary = await asyncio.wait_for(
            NotificationSender(db).send_selected(notification_ids),
            timeout=8,
        )
        await db.commit()
    except TimeoutError:
        await db.rollback()
        return {
            "status": "queued",
            "requested": len(notification_ids),
            "processed": 0,
            "sent": 0,
            "retry": len(notification_ids),
            "failed": 0,
            "reason": "telegram_timeout",
        }
    except Exception:
        await db.rollback()
        return {
            "status": "queued",
            "requested": len(notification_ids),
            "processed": 0,
            "sent": 0,
            "retry": len(notification_ids),
            "failed": 0,
            "reason": "delivery_error",
        }

    requested = int(summary.get("requested", len(notification_ids)))
    sent = int(summary.get("sent", 0))
    retry = int(summary.get("retry", 0))
    failed = int(summary.get("failed", 0))
    processed = int(summary.get("processed", 0))
    if requested and sent >= requested:
        status = "delivered"
    elif retry:
        status = "queued"
    elif failed:
        status = "failed"
    elif processed == 0:
        status = "already_processing"
    else:
        status = "queued"
    return {"status": status, **summary}


async def _fresh_notification(
    db: AsyncSession,
    notification_id: int,
) -> Notification | None:
    return (
        await db.execute(
            select(Notification)
            .where(Notification.id == int(notification_id))
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


def _admin_token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


@router.get("")
async def delivery_queue(
    filter: str = "attention",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    try:
        return await NotificationDeliveryService(db).list_delivery(
            filter_name=filter,
            limit=limit,
        )
    except NotificationDeliveryError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/retry-due")
async def retry_due_notifications(
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = require_admin(x_admin_token)
    requested_limit = (payload or {}).get("limit", 50)
    try:
        ids = await NotificationDeliveryService(db).due_ids(limit=requested_limit)
        if ids:
            db.add(
                AuditLog(
                    actor_type="admin",
                    actor_id=actor_id_from_token(actor),
                    action="TELEGRAM_NOTIFICATION_DUE_BATCH_REQUESTED",
                    entity_type="notification_batch",
                    entity_id=None,
                    old_value=None,
                    new_value={
                        "notification_ids": list(ids),
                        "count": len(ids),
                    },
                    comment="Администратор запустил доставку доступных Telegram-уведомлений",
                )
            )
            await db.commit()
    except (TypeError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Некорректный лимит отправки") from error
    except Exception:
        await db.rollback()
        raise
    delivery = await _attempt_delivery(db, ids)
    return {
        "ok": True,
        "notification_ids": list(ids),
        "delivery": delivery,
        "summary": await NotificationDeliveryService(db).summary(),
    }


@router.post("/{notification_id}/retry")
async def retry_notification(
    notification_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = require_admin(x_admin_token)
    service = NotificationDeliveryService(db)
    try:
        notification = await service.prepare_retry(
            notification_id=notification_id,
            actor_id=actor_id_from_token(actor),
            expected_status=payload.get("expected_status"),
            expected_updated_at=payload.get("expected_updated_at"),
        )
        await db.commit()
        delivery = await _attempt_delivery(db, (int(notification.id),))
        current = await _fresh_notification(db, notification.id)
        if current is None:
            raise NotificationDeliveryError("Уведомление исчезло после отправки")
        return {
            "ok": True,
            "delivery": delivery,
            "item": serialize_notification(current),
            "summary": await service.summary(),
        }
    except NotificationDeliveryError as error:
        await db.rollback()
        status_code = 404 if "не найдено" in str(error).lower() else 409
        raise HTTPException(status_code=status_code, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise


@router.get("/case/{case_id}/ui", response_class=HTMLResponse)
async def notification_case_detail_ui(
    case_id: int,
    request: Request,
    x_admin_token: str | None = Header(default=None),
):
    """Backward-compatible old detail route; fresh UI links to Workdesk instead."""

    try:
        require_admin(_admin_token(request, x_admin_token))
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(
        CASE_DETAIL_HTML.replace("__CASE_ID__", str(int(case_id)))
    )


@router.get("/ui", response_class=HTMLResponse)
async def notification_delivery_ui(
    request: Request,
    x_admin_token: str | None = Header(default=None),
):
    try:
        require_admin(_admin_token(request, x_admin_token))
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(DELIVERY_HTML)


DELIVERY_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Telegram-доставка — Digital Legal Concierge</title>
<style>
:root{--bg:#f4f6fa;--surface:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--primary:#3157d5;--primary-soft:#eef2ff;--green:#14804a;--green-soft:#ecfdf3;--red:#b42318;--red-soft:#fef3f2;--amber:#a15c00;--amber-soft:#fff7e6;--shadow:0 12px 34px rgba(16,24,40,.07)}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}header{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:20px 24px}header .inner{max-width:1220px;margin:auto;display:flex;justify-content:space-between;gap:18px;align-items:center}h1{font-size:23px;margin:0 0 4px}header p{margin:0;color:#d0d5dd;font-size:13px}.links,.row,.filters,.actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.button,button{border:0;border-radius:10px;padding:9px 12px;background:var(--primary);color:#fff;font-weight:750;cursor:pointer;text-decoration:none;display:inline-block}.secondary{background:#475467}.green{background:var(--green)}.amber{background:var(--amber)}button:disabled{opacity:.55;cursor:wait}main{max-width:1220px;margin:auto;padding:22px}.summary-head{display:flex;justify-content:space-between;gap:14px;align-items:end;margin-bottom:12px}.summary-head h2{margin:0 0 4px}.section-label{font-size:11px;font-weight:850;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:6px}.muted{color:var(--muted);font-size:13px}.metrics{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:11px}.metric{background:#fff;border:1px solid var(--line);border-radius:15px;padding:14px;box-shadow:var(--shadow);text-align:left}.metric b{display:block;font-size:25px;margin-bottom:3px}.metric span{color:var(--muted);font-size:12px}.metric.failed{background:var(--red-soft);border-color:#fecdca}.metric.retry{background:var(--amber-soft);border-color:#fedf89}.next-box{background:var(--primary-soft);border:1px solid #c7d2fe;border-radius:13px;padding:12px;margin:12px 0 18px}.toolbar{display:flex;justify-content:space-between;gap:12px;align-items:center;margin:20px 0 10px}.filters button{background:#fff;color:var(--ink);border:1px solid var(--line)}.filters button.active{background:var(--primary);color:#fff;border-color:var(--primary)}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}.card{background:var(--surface);border:1px solid var(--line);border-radius:17px;padding:16px;box-shadow:var(--shadow)}.card.failed{border-color:#fda29b;background:#fffafa}.card.retry{border-color:#fedf89}.card-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.card h3{margin:0 0 4px;font-size:17px}.badge{display:inline-flex;border-radius:999px;padding:5px 9px;background:#eef2f6;font-size:12px;font-weight:750}.badge.failed{background:var(--red-soft);color:var(--red)}.badge.retry,.badge.pending{background:var(--amber-soft);color:var(--amber)}.badge.sent{background:var(--green-soft);color:var(--green)}.message-text{white-space:pre-wrap;line-height:1.45;margin:12px 0;background:#f8fafc;border-radius:11px;padding:10px}.meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.cell{background:#f8fafc;border-radius:10px;padding:9px}.cell span{display:block;color:var(--muted);font-size:11px;margin-bottom:3px}.error-box{background:var(--red-soft);color:var(--red);border-radius:11px;padding:10px;margin:10px 0}.action-box{background:var(--primary-soft);border:1px solid #c7d2fe;border-radius:11px;padding:10px;margin:10px 0}.status{min-height:24px;margin:10px 0}.ok{color:var(--green)}.bad{color:var(--red)}.warn{color:var(--amber)}.empty,.error,.loading{grid-column:1/-1;padding:34px;text-align:center;border:1px dashed var(--line);border-radius:16px;color:var(--muted);background:#fff}.error{color:var(--red);background:var(--red-soft)}@media(max-width:900px){.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.grid{grid-template-columns:1fr}}@media(max-width:620px){header .inner,.summary-head,.toolbar{align-items:flex-start;flex-direction:column}.metrics{grid-template-columns:1fr}.meta{grid-template-columns:1fr}main{padding:14px}}
</style>
</head>
<body>
<header><div class="inner"><div><h1>📨 Telegram-доставка</h1><p>Роль: администратор / суперадминистратор · очередь, ошибки и безопасные повторы · <span id="timeContext">время загружается…</span></p></div><div class="links"><a class="button secondary" href="/admin/workdesk/ui">Рабочий стол</a><a class="button secondary" href="/operator">Все разделы</a></div></div></header>
<main>
<div class="summary-head"><div><div class="section-label">Сейчас</div><h2>Состояние доставки</h2><div id="freshness" class="muted"></div></div><div class="actions"><button id="retryDue" class="green" onclick="retryDue(this)">Отправить доступные сейчас</button><button class="secondary" onclick="load()">Обновить</button></div></div>
<div id="metrics" class="metrics"></div>
<div id="deliveryNext" class="next-box"><b>Главный следующий шаг</b><div class="muted">Проверяем очередь доставки.</div></div>
<div class="toolbar"><div><div class="section-label">Вторичные действия</div><div class="filters"><button data-filter="attention" class="active" onclick="setFilter('attention',this)">Требуют внимания</button><button data-filter="failed" onclick="setFilter('failed',this)">Не доставлено</button><button data-filter="retry" onclick="setFilter('retry',this)">На повторе</button><button data-filter="pending" onclick="setFilter('pending',this)">Ожидают</button><button data-filter="sent" onclick="setFilter('sent',this)">Доставлено</button><button data-filter="all" onclick="setFilter('all',this)">Все</button></div></div></div>
<div id="status" class="status" role="status" aria-live="polite"></div><div id="grid" class="grid"><div class="loading">Загрузка очереди…</div></div>
</main>
<script>
let token='',currentFilter='attention',loadController=null,businessTimeZone='UTC',businessTimeLabel='UTC';const pending=new Set();const grid=document.getElementById('grid'),metrics=document.getElementById('metrics'),statusBox=document.getElementById('status'),freshness=document.getElementById('freshness'),deliveryNext=document.getElementById('deliveryNext'),timeContext=document.getElementById('timeContext');
function esc(v){return String(v??'').replace(/[&<>\x22\x27]/g,c=>c==='&'?'&amp;':c==='<'?'&lt;':c==='>'?'&gt;':c.charCodeAt(0)===34?'&quot;':'&#39;')}
function feedback(text,state=''){statusBox.textContent=text;statusBox.className='status '+state}
function dt(v){if(!v)return '—';try{const rendered=new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short',timeZone:businessTimeZone}).format(new Date(v));return businessTimeLabel?rendered+' '+businessTimeLabel:rendered}catch(_){return String(v)}}
function targetState(x){if(x.target_available)return 'Telegram подключён';if(x.target_recoverable)return 'адрес будет найден из дела или профиля';return 'получатель не связан с системой'}
async function api(path,opts={}){const r=await fetch(path,{...opts,credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});if(r.status===401||r.status===403){location.href='/login';throw new Error('Сессия истекла или недостаточно прав')}const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка запроса');return d}
async function withAction(key,button,work,label='Выполняется…'){if(pending.has(key))return;pending.add(key);const old=button?.textContent;if(button){button.disabled=true;button.setAttribute('aria-busy','true');button.textContent=label}try{return await work()}finally{pending.delete(key);if(button){button.disabled=false;button.removeAttribute('aria-busy');button.textContent=old}}}
function setFilter(name,button){currentFilter=name;document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('active',x===button));void load()}
function renderMetrics(s){metrics.innerHTML=`<article class="metric failed"><b>${s.failed||0}</b><span>не доставлено</span></article><article class="metric retry"><b>${s.retry||0}</b><span>на повторе</span></article><article class="metric"><b>${s.pending||0}</b><span>ожидают отправки</span></article><article class="metric"><b>${s.due_now||0}</b><span>можно отправить сейчас</span></article><article class="metric"><b>${s.sent_recent||0}</b><span>доставлено за 24 часа</span></article>`;document.getElementById('retryDue').disabled=!(s.due_now>0);if((s.failed||0)>0)deliveryNext.innerHTML='<b>Главный следующий шаг</b><div class="bad">Разберите недоставленные уведомления: откройте конкретную карточку, проверьте получателя и выполните безопасный повтор только после проверки причины.</div>';else if((s.due_now||0)>0)deliveryNext.innerHTML=`<b>Главный следующий шаг</b><div>В очереди доступно для отправки сейчас: ${s.due_now}. Запустите подтверждённую отправку или проверьте отдельные карточки.</div>`;else if((s.retry||0)>0)deliveryNext.innerHTML='<b>Главный следующий шаг</b><div class="warn">Есть уведомления на повторе, но сейчас срок следующей попытки ещё не наступил. Проверьте время и причину предыдущей ошибки.</div>';else deliveryNext.innerHTML='<b>Главный следующий шаг</b><div class="ok">Критических действий по доставке сейчас не требуется.</div>'}
function badgeClass(x){return String(x.status||'').toLowerCase()}
function card(x){const failure=x.last_error?`<div class="error-box"><b>Причина</b><br>${esc(x.last_error)}</div>`:'';const retry=x.can_retry?`<button data-notification-id="${x.id}" data-expected-status="${esc(x.status)}" data-expected-updated-at="${esc(x.updated_at)}" onclick="retryOne(${x.id},this)">${esc(x.retry_label||'Повторить сейчас')}</button>`:'';const caseLink=x.case_id?`<a class="button secondary" href="/admin/workdesk/ui?case_id=${x.case_id}">Открыть дело #${x.case_id}</a>`:'';return `<article class="card ${badgeClass(x)}" id="notification_${x.id}"><div class="card-head"><div><h3>${esc(x.title||'Telegram-уведомление')}</h3><div class="muted">${esc(x.event)} · дело ${esc(x.case_id||'—')}</div></div><span class="badge ${badgeClass(x)}">${esc(x.status_label)}</span></div><div class="message-text">${esc(x.text)}</div>${failure}<div class="action-box"><b>Главный следующий шаг</b><br>${esc(x.recommended_action)}</div><div class="meta"><div class="cell"><span>Получатель</span>${esc(x.recipient)} · ${esc(targetState(x))}</div><div class="cell"><span>Попытки</span>${esc(x.attempt_count)}</div><div class="cell"><span>Создано / отправлено</span>${esc(dt(x.created_at))} / ${esc(dt(x.sent_at))}</div><div class="cell"><span>Следующая попытка</span>${esc(dt(x.next_attempt_at))}</div></div><div class="actions" style="margin-top:11px">${retry}${caseLink}</div></article>`}
function showError(e){grid.innerHTML=`<div class="error"><b>Не удалось загрузить доставку</b><p>${esc(e.message||e)}</p><button onclick="load()">Повторить</button></div>`;deliveryNext.innerHTML='<b>Главный следующий шаг</b><div class="bad">Повторите загрузку. Если ошибка сохраняется, вернитесь в рабочий стол и не запускайте массовую отправку вслепую.</div>';feedback(e.message||String(e),'bad')}
async function load(){if(loadController)loadController.abort();const controller=new AbortController();loadController=controller;grid.innerHTML='<div class="loading">Загрузка очереди…</div>';feedback('');try{const d=await api('/admin/notification-delivery?filter='+encodeURIComponent(currentFilter),{signal:controller.signal});renderMetrics(d.summary||{});freshness.textContent='Обновлено '+dt(d.generated_at);grid.innerHTML=d.items?.length?d.items.map(card).join(''):`<div class="empty"><b>Записей нет</b><p>Для выбранного фильтра очередь пуста.</p><button onclick="load()">Обновить</button></div>`}catch(e){if(e.name!=='AbortError')showError(e)}finally{if(loadController===controller)loadController=null}}
function deliveryMessage(d){const status=d?.status||'queued';if(status==='delivered')return ['Сообщение доставлено в Telegram','ok'];if(status==='failed')return ['Повтор выполнен, но Telegram отклонил сообщение','warn'];if(status==='already_processing')return ['Сообщение уже обрабатывается другим процессом','warn'];return ['Повтор сохранён и поставлен в очередь доставки','warn']}
async function retryOne(id,button){const expectedStatus=button.dataset.expectedStatus||'',expectedUpdatedAt=button.dataset.expectedUpdatedAt||'',label=button.textContent||'Повторить сейчас';if(!confirm(`${label} для уведомления #${id}?`))return;return withAction('notification:'+id,button,async()=>{let d;try{d=await api('/admin/notification-delivery/'+id+'/retry',{method:'POST',body:JSON.stringify({expected_status:expectedStatus,expected_updated_at:expectedUpdatedAt})})}catch(e){feedback('Повтор не выполнен: '+e.message,'bad');return}const [text,state]=deliveryMessage(d.delivery);feedback(text,state);try{await load()}catch(e){feedback(text+', но экран не обновился: '+e.message,'warn')}},'Отправка…')}
async function retryDue(button){if(!confirm('Отправить все доступные сейчас PENDING/RETRY уведомления (не более 50)?'))return;return withAction('retry-due',button,async()=>{let d;try{d=await api('/admin/notification-delivery/retry-due',{method:'POST',body:JSON.stringify({limit:50})})}catch(e){feedback('Массовая отправка не выполнена: '+e.message,'bad');return}const x=d.delivery||{};feedback(`Обработано: ${x.processed||0}, доставлено: ${x.sent||0}, повтор: ${x.retry||0}, ошибок: ${x.failed||0}`,x.failed?'warn':'ok');try{await load()}catch(e){feedback('Отправка выполнена, но экран не обновился: '+e.message,'warn')}},'Отправка…')}
async function boot(){try{const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();const roles=s.roles||[s.role];if(!(roles.includes('admin')||roles.includes('superadmin'))){throw new Error('Требуется роль администратора или суперадминистратора')}token=s.api_token||'';businessTimeZone=s.business_timezone||'UTC';businessTimeLabel=s.business_timezone_label||businessTimeZone;timeContext.textContent='время: '+businessTimeLabel;await load()}catch(e){showError(e)}}
boot();
</script>
</body>
</html>
"""