from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.cases.sla_service import CaseSLAError, CaseSLAService
from app.domain.documents.document_review_context import (
    build_document_review_case_context,
)
from app.domain.documents.document_review_service import (
    DECISION_LABELS,
    DocumentReviewError,
    DocumentReviewService,
)
from app.domain.notifications.notification_sender import NotificationSender
from app.security.document_access import (
    DocumentAccessError,
    resolve_document_actor,
)

router = APIRouter(prefix="/review", tags=["document-review"])


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _deliver_review_notifications(
    db: AsyncSession,
    notification_ids: tuple[int, ...],
) -> dict[str, object]:
    if not notification_ids:
        return {
            "status": "not_required",
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
        # The legal decision was committed before this helper was called.
        # Only delivery state is rolled back; the scheduler can retry the
        # still-pending outbox records on its next cycle.
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
    if requested and sent >= requested:
        status = "delivered"
    elif retry:
        status = "queued"
    elif failed:
        status = "unavailable"
    else:
        status = "queued"
    return {"status": status, **summary}


@router.get("/queue")
async def review_queue(
    request: Request,
    case_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        actor = await resolve_document_actor(db, _token(request, x_admin_token))
        service = DocumentReviewService(db)
        items = await service.queue(
            actor=actor,
            case_id=case_id,
        )
        case_context = None
        if case_id is not None:
            case_context = await build_document_review_case_context(
                db,
                actor=actor,
                case_id=case_id,
            )
        return {
            "role": actor.role,
            "scoped": case_id is not None,
            "count": len(items),
            "items": items,
            "case_context": case_context,
        }
    except DocumentAccessError:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post("/documents/{document_id}/decision")
async def review_document(
    document_id: int,
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = None
    try:
        actor = await resolve_document_actor(db, _token(request, x_admin_token))
        result = await DocumentReviewService(db).review(
            actor=actor,
            document_id=document_id,
            decision=payload.get("decision") or "",
            comment=payload.get("comment") or "",
            expected_status=payload.get("expected_status"),
            expected_version=payload.get("expected_version"),
            expected_updated_at=payload.get("expected_updated_at"),
        )
        if actor.role == "lawyer" and result.changed:
            await CaseSLAService(db).record_lawyer_activity(
                case=result.case,
                lawyer_id=actor.lawyer_id or 0,
                action="DOCUMENT_REVIEW_DECISION",
                comment=(
                    f"Документ #{result.document.id}: "
                    f"{DECISION_LABELS[result.decision]}"
                ),
            )

        # The legal operation and its outbox records become durable first.
        # Telegram delivery is deliberately a second transaction, so a network
        # failure can never reverse the lawyer's decision.
        await db.commit()
        await db.refresh(result.document)
        await db.refresh(result.case)
        delivery = await _deliver_review_notifications(db, result.notification_ids)

        return {
            "ok": True,
            "changed": result.changed,
            "document_id": result.document.id,
            "case_id": result.case.id,
            "case_status": result.case.status,
            "case_updated_at": result.case.updated_at.isoformat(),
            "status": result.document.status,
            "status_label": DECISION_LABELS[result.decision],
            "version": result.document.version,
            "updated_at": result.document.updated_at.isoformat(),
            "delivery": delivery,
        }
    except DocumentAccessError:
        await db.rollback()
        raise
    except DocumentReviewError as error:
        await db.rollback()
        status_code = 404 if "не найден" in str(error).lower() else 409
        raise HTTPException(status_code=status_code, detail=str(error)) from error
    except CaseSLAError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise


@router.get("/ui", response_class=HTMLResponse)
async def document_review_ui():
    return HTMLResponse(REVIEW_HTML)


REVIEW_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Проверка документов</title>
<style>
:root{--bg:#f4f6fa;--surface:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--primary:#3157d5;--primary-soft:#eef2ff;--green:#14804a;--green-soft:#ecfdf3;--red:#b42318;--red-soft:#fef3f2;--amber:#a15c00;--amber-soft:#fff7e6;--shadow:0 12px 32px rgba(16,24,40,.07)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}header{position:sticky;top:0;z-index:10;background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:18px 24px;box-shadow:0 4px 18px rgba(16,24,40,.12)}header .inner{max-width:1180px;margin:auto;display:flex;align-items:center;justify-content:space-between;gap:16px}h1{font-size:22px;margin:0 0 4px}header p{margin:0;color:#d0d5dd;font-size:13px}.links,.actions,.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.button,button{border:0;border-radius:10px;padding:9px 12px;background:var(--primary);color:#fff;font-weight:750;cursor:pointer;text-decoration:none;display:inline-block}.secondary{background:#475467}.green{background:var(--green)}.amber{background:var(--amber)}.red{background:var(--red)}button:disabled,textarea:disabled,input:disabled{opacity:.55;cursor:wait}main{max-width:1180px;margin:auto;padding:22px}.summary{display:flex;justify-content:space-between;align-items:end;gap:16px;margin-bottom:14px}.summary h2{margin:0 0 4px}.summary p{margin:0;color:var(--muted)}.counter{background:var(--primary-soft);color:#2445b5;border-radius:999px;padding:8px 12px;font-weight:800}.case-panel{display:none;background:#fff;border:1px solid #c7d2fe;border-radius:18px;padding:16px;margin:0 0 15px;box-shadow:var(--shadow)}.case-panel.active{display:block}.case-head{display:flex;justify-content:space-between;align-items:flex-start;gap:14px}.case-head h2{margin:0 0 4px;font-size:19px}.badge{display:inline-flex;border-radius:999px;padding:5px 9px;background:var(--amber-soft);color:var(--amber);font-size:12px;font-weight:750}.next-box{background:var(--primary-soft);border:1px solid #c7d2fe;border-radius:13px;padding:13px;margin:12px 0}.next-box span,.section-label{display:block;color:var(--muted);font-size:11px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;margin-bottom:5px}.next-box b{display:block;margin-bottom:4px}.case-metrics{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px}.metric,.cell{background:#f8fafc;border-radius:10px;padding:9px}.metric span,.cell span{display:block;color:var(--muted);font-size:11px;margin-bottom:3px}.accept-form{display:none;border-top:1px solid var(--line);margin-top:13px;padding-top:13px}.accept-form.open{display:block}.accept-review{display:none;background:#f8fafc;border:1px solid var(--line);border-radius:12px;padding:12px;margin-top:9px}.accept-review.open{display:block}.accept-form textarea,.review-form textarea{width:100%;min-height:90px;border:1px solid #d0d5dd;border-radius:10px;padding:10px;resize:vertical}.toolbar{justify-content:space-between;margin:14px 0}.search{width:min(420px,100%);border:1px solid #d0d5dd;border-radius:10px;padding:10px 12px;background:#fff}.scope{display:none;background:var(--primary-soft);border:1px solid #c7d2fe;border-radius:13px;padding:11px 13px;margin-bottom:12px;align-items:center;justify-content:space-between;gap:12px}.scope.active{display:flex}.scope b{display:block;margin-bottom:2px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.card{background:var(--surface);border:1px solid var(--line);border-radius:17px;padding:16px;box-shadow:var(--shadow)}.card.primary{border-color:#9bb0ff}.card-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.card h3{margin:0 0 5px;font-size:17px}.meta{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:13px 0}.review-form{display:none;margin-top:13px;padding-top:13px;border-top:1px solid var(--line)}.review-form.open{display:block}.review-form h4{margin:0 0 7px}.decision-review{display:none;background:#f8fafc;border:1px solid var(--line);border-radius:12px;padding:11px;margin-top:9px}.decision-review.open{display:block}.decision-review p{white-space:pre-wrap;word-break:break-word}.hint{color:var(--muted);font-size:12px;margin:6px 0 9px}.message{min-height:22px;margin-bottom:10px}.ok{color:var(--green)}.bad{color:var(--red)}.warn{color:var(--amber)}.empty,.error,.loading{grid-column:1/-1;padding:34px;text-align:center;border:1px dashed var(--line);border-radius:16px;color:var(--muted);background:#fff}.error{color:var(--red);background:var(--red-soft)}
@media(max-width:880px){.case-metrics{grid-template-columns:repeat(3,1fr)}}@media(max-width:780px){header .inner,.summary,.scope,.case-head{align-items:flex-start;flex-direction:column}.grid{grid-template-columns:1fr}.meta{grid-template-columns:1fr}.case-metrics{grid-template-columns:1fr 1fr}main{padding:14px}}@media(max-width:520px){.links .button{flex:1;text-align:center}.case-metrics{grid-template-columns:1fr}.toolbar{align-items:stretch;flex-direction:column}.search{width:100%}}
</style>
</head>
<body>
<header><div class="inner"><div><h1>📄 Проверка документов</h1><p id="roleLabel">Защищённая очередь решений по файлам</p></div><div class="links"><a class="button secondary" href="/lawyer/workspace/ui">Кабинет юриста</a><a class="button secondary" href="/lawyer/consultation-desk/ui">Консультации</a><a class="button secondary" href="/message-center/ui">Сообщения</a><a class="button secondary" href="/operator">Все разделы</a></div></div></header>
<main>
<div class="summary"><div><h2>Ожидают решения</h2><p>Сначала текущий следующий шаг дела, затем решение по конкретному файлу.</p></div><span id="counter" class="counter">0</span></div>
<section id="casePanel" class="case-panel" aria-live="polite"></section>
<div id="scope" class="scope"><div><b id="scopeTitle">Документы выбранного дела</b><span id="scopeDetail" class="hint">Показана только очередь этого дела.</span></div><a class="button secondary" href="/document-access/review/ui">Вся очередь</a></div>
<div id="toolbar" class="toolbar"><input id="search" class="search" type="search" placeholder="Найти по делу, клиенту, документу" oninput="renderDocuments()"><button class="secondary" onclick="load()">Обновить</button></div>
<div id="message" class="message" role="status" aria-live="polite"></div><div id="grid" class="grid"><div class="loading">Загрузка очереди…</div></div>
</main>
<script>
let token='',role='',context=null,items=new Map(),rawItems=[];const pending=new Set(),drafts=new Map(),caseDrafts=new Map();const params=new URLSearchParams(location.search),rawCaseId=params.get('case_id'),caseId=/^[1-9]\d*$/.test(rawCaseId||'')?Number(rawCaseId):null,grid=document.getElementById('grid'),message=document.getElementById('message'),counter=document.getElementById('counter'),roleLabel=document.getElementById('roleLabel'),scope=document.getElementById('scope'),scopeTitle=document.getElementById('scopeTitle'),scopeDetail=document.getElementById('scopeDetail'),casePanel=document.getElementById('casePanel'),search=document.getElementById('search');
function esc(v){return String(v??'').replace(/[&<>\x22\x27]/g,c=>c==='&'?'&amp;':c==='<'?'&lt;':c==='>'?'&gt;':c.charCodeAt(0)===34?'&quot;':'&#39;')}
function feedback(text,state=''){message.textContent=text;message.className='message '+state}
function dt(v){return v?new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short'}).format(new Date(v)):'—'}
function size(v){if(v===null||v===undefined)return '—';if(v<1024)return v+' Б';if(v<1048576)return (v/1024).toFixed(1)+' КБ';return (v/1048576).toFixed(1)+' МБ'}
function deliveryText(delivery){const status=delivery?.status||'queued';if(status==='delivered')return ['Клиент уведомлён в Telegram','ok'];if(status==='queued')return ['Уведомление поставлено в очередь повторной доставки','warn'];if(status==='unavailable')return ['Решение сохранено, но Telegram-доставка недоступна','warn'];return ['Решение сохранено. Повторное уведомление не требуется','ok']}
function queuePath(){return caseId?'/document-access/review/queue?case_id='+encodeURIComponent(caseId):'/document-access/review/queue'}
function draftKey(id,decision){return `${id}:${decision}`}
function caseDraftKey(){return String(caseId||context?.case_id||'')}
function safeHref(value,fallback='/lawyer/workspace/ui'){const x=String(value||'');return x.startsWith('/')&&!x.startsWith('//')?x:fallback}
async function api(path,opts={}){const r=await fetch(path,{...opts,credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});if(r.status===401||r.status===403){location.href='/login';const e=new Error('Сессия истекла или недостаточно прав');e.status=r.status;throw e}const d=await r.json().catch(()=>({}));if(!r.ok){const e=new Error(d.detail||'Ошибка запроса');e.status=r.status;throw e}return d}
async function boot(){try{const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();const roles=s.roles||[s.role];if(!roles.some(x=>['admin','superadmin','lawyer'].includes(x))){throw new Error('Требуется роль администратора или юриста')}token=s.api_token||'';roleLabel.textContent=roles.includes('lawyer')&&!roles.includes('admin')?'Документы назначенных вам дел':'Все документы, переданные на юридическую проверку';if(caseId)scope.classList.add('active');await load()}catch(e){showError(e)}}
function showError(e){grid.innerHTML=`<div class="error"><b>Не удалось загрузить очередь</b><p>${esc(e.message||e)}</p><div class="actions" style="justify-content:center"><button onclick="load()">Повторить</button><a class="button secondary" href="/lawyer/workspace/ui">К кабинету</a></div></div>`;feedback(e.message||String(e),'bad')}
function primaryActionMarkup(c){if(!c)return '';if(c.primary_action==='review_document')return `<button onclick="focusFirstDocument()">${esc(c.primary_label)}</button>`;if(c.primary_action==='accept_m1_case'&&c.can_accept)return `<button class="green" onclick="openAcceptForm()">${esc(c.primary_label)}</button>`;if(['wait_client_reupload','wait_client_submit'].includes(c.primary_action))return `<button class="secondary" onclick="load()">Проверить обновления</button>`;return `<a class="button" href="${esc(safeHref(c.workspace_url))}">${esc(c.primary_label||'Открыть дело')}</a>`}
function renderCaseContext(){if(!caseId){casePanel.classList.remove('active');return}casePanel.classList.add('active');if(!context){casePanel.innerHTML=`<div class="case-head"><div><h2>Документы выбранного дела</h2><div class="hint">Дело не найдено или дело недоступно в вашей роли.</div></div></div><div class="actions" style="margin-top:12px"><a class="button secondary" href="/document-access/review/ui">Вся очередь</a><a class="button secondary" href="/lawyer/workspace/ui">К кабинету</a></div>`;return}scopeTitle.textContent='Документы дела '+context.case_number;scopeDetail.textContent=(context.client_name||'Клиент')+' · '+(context.case_status_label||'текущее состояние дела');casePanel.innerHTML=`<div class="case-head"><div><span class="section-label">Сейчас</span><h2>${esc(context.case_number)} · ${esc(context.client_name||'Клиент')}</h2><div class="hint">${esc(context.case_status_label||context.case_status)} · обновлено ${esc(dt(context.case_updated_at))}</div></div><span class="badge">${esc(context.route||'Дело')}</span></div><div class="next-box"><span>Главный следующий шаг</span><b>${esc(context.primary_label)}</b><div>${esc(context.primary_note||'')}</div><div class="actions" style="margin-top:10px">${primaryActionMarkup(context)}<a class="button secondary" href="${esc(safeHref(context.message_url,'/message-center/ui'))}">Переписка</a></div></div><span class="section-label">Комплект документов</span><div class="case-metrics"><div class="metric"><span>Актуальных типов</span><b>${esc(context.documents_total)}</b></div><div class="metric"><span>На проверке</span><b>${esc(context.documents_on_review)}</b></div><div class="metric"><span>Принято</span><b>${esc(context.documents_approved)}</b></div><div class="metric"><span>Нужна замена</span><b>${esc(context.documents_replacement)}</b></div><div class="metric"><span>Загружено, не передано</span><b>${esc(context.documents_uploaded)}</b></div></div><div id="acceptForm" class="accept-form"><div id="acceptEdit"><h3>Принять M1 и открыть этап договора</h3><textarea id="acceptComment" oninput="caseDrafts.set(caseDraftKey(),this.value)" placeholder="Комментарий клиенту (необязательно)"></textarea><div class="hint">Принятие не происходит автоматически после последнего документа. Сначала проверьте действие.</div><div class="actions"><button onclick="reviewAccept()">Проверить действие</button><button class="secondary" onclick="closeAcceptForm()">Закрыть</button></div></div><div id="acceptReview" class="accept-review"><b>Подтвердите переход дела</b><p>Все актуальные документы уже приняты. После подтверждения M1 будет принят и откроется этап подготовки договора.</p><div class="hint">Комментарий клиенту:</div><p id="acceptReviewComment"></p><div class="actions"><button class="green" data-case-action="accept" onclick="submitAccept(this)">Подтвердить принятие</button><button class="secondary" onclick="backAcceptEdit()">← Изменить комментарий</button></div></div></div>`;const input=document.getElementById('acceptComment');if(input)input.value=caseDrafts.get(caseDraftKey())||''}
function focusFirstDocument(){const first=rawItems[0];if(!first){feedback('Документов на проверке больше нет. Обновите состояние дела.','warn');return}document.getElementById('card_'+first.document_id)?.scrollIntoView({behavior:'smooth',block:'center'})}
function openAcceptForm(){if(!context?.can_accept){feedback('Дело больше не готово к принятию. Обновите состояние.','warn');return}document.getElementById('acceptForm')?.classList.add('open');document.getElementById('acceptReview')?.classList.remove('open');document.getElementById('acceptEdit').style.display='block';document.getElementById('acceptComment')?.focus()}
function closeAcceptForm(){const input=document.getElementById('acceptComment');if(input)caseDrafts.set(caseDraftKey(),input.value);document.getElementById('acceptForm')?.classList.remove('open')}
function reviewAccept(){const input=document.getElementById('acceptComment'),comment=input?.value||'';caseDrafts.set(caseDraftKey(),comment);document.getElementById('acceptReviewComment').textContent=comment.trim()||'Без комментария';document.getElementById('acceptEdit').style.display='none';document.getElementById('acceptReview').classList.add('open')}
function backAcceptEdit(){document.getElementById('acceptReview').classList.remove('open');document.getElementById('acceptEdit').style.display='block';document.getElementById('acceptComment')?.focus()}
async function submitAccept(button){if(!context?.can_accept||!caseId){feedback('Дело больше не готово к принятию. Обновите состояние.','bad');return}const key='case-accept:'+caseId;if(pending.has(key))return;pending.add(key);button.disabled=true;button.setAttribute('aria-busy','true');const old=button.textContent;button.textContent='Сохранение…';const comment=caseDrafts.get(caseDraftKey())||document.getElementById('acceptComment')?.value||'';try{await api('/lawyer/cases/'+caseId+'/accept',{method:'POST',body:JSON.stringify({comment,expected_status:context.case_status,expected_updated_at:context.case_updated_at})});caseDrafts.delete(caseDraftKey());try{await load(false,true);feedback('Дело принято. Этап договора открыт.','ok')}catch(e){feedback('Дело принято, но кабинет не обновился: '+e.message,'warn')}}catch(e){if(e.status===409){caseDrafts.set(caseDraftKey(),comment);try{await load(false,true)}catch{}feedback('Дело изменилось. Черновик сохранён — проверьте актуальный следующий шаг.','warn')}else{feedback('Дело не принято: '+e.message,'bad')}}finally{pending.delete(key);button.disabled=false;button.removeAttribute('aria-busy');button.textContent=old}}
async function load(resetFeedback=true,propagate=false){grid.innerHTML='<div class="loading">Загрузка очереди…</div>';if(resetFeedback)feedback('');try{const d=await api(queuePath());role=d.role||'';context=d.case_context||null;rawItems=d.items||[];items=new Map(rawItems.map(x=>[x.document_id,x]));counter.textContent=d.count||0;if(caseId){scope.classList.add('active')}renderCaseContext();renderDocuments()}catch(e){if(propagate)throw e;showError(e)}}
function renderDocuments(){let rows=rawItems;const q=String(search?.value||'').trim().toLowerCase();if(q)rows=rows.filter(x=>[x.case_number,x.client_name,x.title,x.document_type,x.file_name].some(v=>String(v||'').toLowerCase().includes(q)));if(!rows.length){if(rawItems.length&&q){grid.innerHTML=`<div class="empty"><b>Поиск ничего не нашёл</b><p>Очистите запрос — документы останутся в очереди.</p><button onclick="search.value='';renderDocuments()">Очистить поиск</button></div>`;return}const title=caseId?'По выбранному делу документов на проверке нет':'Очередь пуста';let detail=caseId?'Текущий следующий шаг показан выше. Если клиенту нужна новая версия, ожидайте её здесь — дело не потеряется.':'Все переданные документы уже получили решение.';if(caseId&&!context)detail='Документы уже получили решение, ещё не переданы на проверку или дело недоступно в вашей роли.';grid.innerHTML=`<div class="empty"><b>${esc(title)}</b><p>${esc(detail)}</p><div class="actions" style="justify-content:center"><button onclick="load()">Обновить</button>${caseId?'<a class="button secondary" href="/document-access/review/ui">Вся очередь</a>':''}</div></div>`;return}grid.innerHTML=rows.map((x,index)=>card(x,index===0&&caseId)).join('')}
function card(x,isPrimary=false){return `<article class="card ${isPrimary?'primary':''}" id="card_${x.document_id}"><div class="card-head"><div><h3>${esc(x.title)}</h3><div>${esc(x.case_number)} · ${esc(x.client_name||'Клиент')}</div></div><span class="badge">На проверке</span></div><div class="meta"><div class="cell"><span>Файл</span>${esc(x.file_name)}</div><div class="cell"><span>Тип</span>${esc(x.document_type)}</div><div class="cell"><span>Версия</span>${esc(x.version)}</div><div class="cell"><span>Размер / загрузка</span>${esc(size(x.file_size))} · ${esc(dt(x.created_at))}</div></div><div class="actions"><button data-document-id="${x.document_id}" onclick="downloadDocument(${x.document_id},this)">Скачать</button><button data-document-id="${x.document_id}" class="green" onclick="openForm(${x.document_id},'approve','Принять документ')">Принять</button><button data-document-id="${x.document_id}" class="amber" onclick="openForm(${x.document_id},'request_reupload','Запросить новую версию')">Новая версия</button><button data-document-id="${x.document_id}" class="red" onclick="openForm(${x.document_id},'reject','Отклонить документ')">Отклонить</button></div><div class="review-form" id="form_${x.document_id}" data-stage="edit"><div class="decision-edit" id="edit_${x.document_id}"><h4 id="form_title_${x.document_id}"></h4><textarea id="comment_${x.document_id}" oninput="rememberDraft(${x.document_id},this)" placeholder="Комментарий клиенту"></textarea><div id="hint_${x.document_id}" class="hint"></div><div class="actions"><button data-document-id="${x.document_id}" onclick="reviewDecision(${x.document_id},this)">Проверить решение</button><button class="secondary" onclick="closeForm(${x.document_id})">Закрыть</button></div></div><div class="decision-review" id="decision_review_${x.document_id}"><b id="decision_review_title_${x.document_id}"></b><p id="decision_review_effect_${x.document_id}"></p><div class="hint">Комментарий клиенту:</div><p id="decision_review_comment_${x.document_id}"></p><div class="actions"><button data-document-id="${x.document_id}" id="submit_${x.document_id}" onclick="submitDecision(${x.document_id},this)">Подтвердить решение</button><button class="secondary" onclick="backDecisionEdit(${x.document_id})">← Изменить комментарий</button></div></div></div></article>`}
function controls(id){return Array.from(document.querySelectorAll(`[data-document-id="${id}"]`))}
async function withDocument(id,button,work,label='Выполняется…'){if(pending.has(id))return;pending.add(id);const list=controls(id),labels=new Map(list.filter(x=>x.tagName==='BUTTON').map(x=>[x,x.textContent]));list.forEach(x=>{x.disabled=true;x.setAttribute('aria-busy','true')});if(button)button.textContent=label;try{return await work()}finally{pending.delete(id);list.forEach(x=>{x.disabled=false;x.removeAttribute('aria-busy')});labels.forEach((v,x)=>x.textContent=v)}}
function rememberDraft(id,textarea){const form=document.getElementById('form_'+id),decision=form?.dataset.decision||'';if(decision)drafts.set(draftKey(id,decision),textarea.value)}
function openForm(id,decision,title){document.querySelectorAll('.review-form').forEach(x=>x.classList.remove('open'));const form=document.getElementById('form_'+id);form.dataset.decision=decision;form.dataset.stage='edit';document.getElementById('form_title_'+id).textContent=title;document.getElementById('comment_'+id).value=drafts.get(draftKey(id,decision))||'';document.getElementById('hint_'+id).textContent=decision==='approve'?'Комментарий необязателен.':'Обязательно укажите понятную причину — минимум 10 символов.';document.getElementById('edit_'+id).style.display='block';document.getElementById('decision_review_'+id).classList.remove('open');form.classList.add('open');document.getElementById('comment_'+id).focus()}
function closeForm(id){const form=document.getElementById('form_'+id),decision=form?.dataset.decision||'',textarea=document.getElementById('comment_'+id);if(decision&&textarea)drafts.set(draftKey(id,decision),textarea.value);form.classList.remove('open')}
function reviewDecision(id){const form=document.getElementById('form_'+id),decision=form.dataset.decision||'',comment=document.getElementById('comment_'+id).value.trim();if(['request_reupload','reject'].includes(decision)&&comment.length<10){feedback('Укажите причину — минимум 10 символов.','bad');return}drafts.set(draftKey(id,decision),comment);const labels={approve:'Принять документ',request_reupload:'Запросить новую версию',reject:'Отклонить документ'};const effects={approve:'Документ станет принятым. Если это последний нерешённый файл M1, следующим шагом будет отдельное принятие дела.',request_reupload:'Клиент получит конкретное замечание и кнопку загрузки новой версии этого документа.',reject:'Документ будет отклонён. Клиент увидит причину и безопасный путь к новой версии.'};document.getElementById('decision_review_title_'+id).textContent=labels[decision]||'Решение';document.getElementById('decision_review_effect_'+id).textContent=effects[decision]||'';document.getElementById('decision_review_comment_'+id).textContent=comment||'Без комментария';document.getElementById('edit_'+id).style.display='none';document.getElementById('decision_review_'+id).classList.add('open');form.dataset.stage='review'}
function backDecisionEdit(id){const form=document.getElementById('form_'+id);document.getElementById('decision_review_'+id).classList.remove('open');document.getElementById('edit_'+id).style.display='block';form.dataset.stage='edit';document.getElementById('comment_'+id)?.focus()}
async function downloadDocument(id,button){return withDocument(id,button,async()=>{try{const d=await api('/document-access/documents/'+id+'/grant',{method:'POST',body:'{}'});feedback('Одноразовая ссылка подготовлена. Начинается скачивание.','ok');window.location.assign(d.download_url)}catch(e){feedback('Документ не скачан: '+e.message,'bad')}},'Подготовка…')}
async function submitDecision(id,button){const x=items.get(id),form=document.getElementById('form_'+id),decision=form.dataset.decision||'',comment=document.getElementById('comment_'+id).value.trim();if(!x){feedback('Документ уже отсутствует в текущей очереди. Обновите список.','bad');return}if(form.dataset.stage!=='review'){feedback('Сначала проверьте действие перед сохранением.','bad');return}if(['request_reupload','reject'].includes(decision)&&comment.length<10){feedback('Укажите причину — минимум 10 символов.','bad');return}return withDocument(id,button,async()=>{let d;try{d=await api('/document-access/review/documents/'+id+'/decision',{method:'POST',body:JSON.stringify({decision,comment,expected_status:x.status,expected_version:x.version,expected_updated_at:x.updated_at})})}catch(e){if(e.status===409){drafts.set(draftKey(id,decision),comment);try{await load(false,true)}catch{}feedback('Документ изменился. Черновик сохранён — проверьте актуальную версию.','warn')}else{feedback('Решение не сохранено: '+e.message,'bad')}return}drafts.delete(draftKey(id,decision));const [deliveryMessage,deliveryState]=deliveryText(d.delivery);const success=(d.changed?`Решение сохранено: ${d.status_label}. `:`Решение уже было сохранено: ${d.status_label}. `)+deliveryMessage;try{await load(false,true);feedback(success,deliveryState)}catch(e){feedback('Решение сохранено, но очередь не обновилась: '+e.message,'warn')}},'Сохранение…')}
boot();
</script>
</body>
</html>
"""
