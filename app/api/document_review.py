from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.cases.sla_service import CaseSLAError, CaseSLAService
from app.domain.documents.document_review_service import (
    DECISION_LABELS,
    DocumentReviewError,
    DocumentReviewService,
)
from app.security.document_access import (
    DocumentAccessError,
    resolve_document_actor,
)

router = APIRouter(prefix="/review", tags=["document-review"])


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


@router.get("/queue")
async def review_queue(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        actor = await resolve_document_actor(db, _token(request, x_admin_token))
        items = await DocumentReviewService(db).queue(actor=actor)
        return {
            "role": actor.role,
            "count": len(items),
            "items": items,
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
        await db.commit()
        await db.refresh(result.document)
        await db.refresh(result.case)
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
<title>Проверка документов — Digital Legal Concierge</title>
<style>
:root{--bg:#f4f6fa;--surface:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--primary:#3157d5;--primary-soft:#eef2ff;--green:#14804a;--green-soft:#ecfdf3;--red:#b42318;--red-soft:#fef3f2;--amber:#a15c00;--amber-soft:#fff7e6;--shadow:0 12px 32px rgba(16,24,40,.07)}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}header{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:20px 24px}header .inner{max-width:1180px;margin:auto;display:flex;align-items:center;justify-content:space-between;gap:16px}h1{font-size:22px;margin:0 0 4px}header p{margin:0;color:#d0d5dd;font-size:13px}.links{display:flex;gap:8px;flex-wrap:wrap}.button,button{border:0;border-radius:10px;padding:9px 12px;background:var(--primary);color:#fff;font-weight:700;cursor:pointer;text-decoration:none;display:inline-block}.secondary{background:#475467}.green{background:var(--green)}.amber{background:var(--amber)}.red{background:var(--red)}button:disabled,textarea:disabled{opacity:.55;cursor:wait}main{max-width:1180px;margin:auto;padding:22px}.summary{display:flex;justify-content:space-between;align-items:end;gap:16px;margin-bottom:14px}.summary h2{margin:0 0 4px}.summary p{margin:0;color:var(--muted)}.counter{background:var(--primary-soft);color:#2445b5;border-radius:999px;padding:8px 12px;font-weight:800}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.card{background:var(--surface);border:1px solid var(--line);border-radius:17px;padding:16px;box-shadow:var(--shadow)}.card-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.card h3{margin:0 0 5px;font-size:17px}.badge{display:inline-flex;border-radius:999px;padding:5px 9px;background:var(--amber-soft);color:var(--amber);font-size:12px;font-weight:750}.meta{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:13px 0}.cell{background:#f8fafc;border-radius:10px;padding:9px}.cell span{display:block;color:var(--muted);font-size:11px;margin-bottom:3px}.actions{display:flex;gap:7px;flex-wrap:wrap}.review-form{display:none;margin-top:13px;padding-top:13px;border-top:1px solid var(--line)}.review-form.open{display:block}.review-form h4{margin:0 0 7px}.review-form textarea{width:100%;min-height:90px;border:1px solid #d0d5dd;border-radius:10px;padding:10px;resize:vertical}.hint{color:var(--muted);font-size:12px;margin:6px 0 9px}.message{min-height:22px;margin-bottom:10px}.ok{color:var(--green)}.bad{color:var(--red)}.warn{color:var(--amber)}.empty,.error,.loading{grid-column:1/-1;padding:34px;text-align:center;border:1px dashed var(--line);border-radius:16px;color:var(--muted);background:#fff}.error{color:var(--red);background:var(--red-soft)}@media(max-width:780px){header .inner,.summary{align-items:flex-start;flex-direction:column}.grid{grid-template-columns:1fr}.meta{grid-template-columns:1fr}main{padding:14px}}
</style>
</head>
<body>
<header><div class="inner"><div><h1>📄 Проверка документов</h1><p id="roleLabel">Защищённая очередь решений по файлам</p></div><div class="links"><a class="button secondary" href="/lawyer/ui">Кабинет юриста</a><a class="button secondary" href="/admin-ui">Админка</a><a class="button secondary" href="/operator">Все разделы</a></div></div></header>
<main><div class="summary"><div><h2>Ожидают решения</h2><p>Скачайте документ, проверьте содержимое и зафиксируйте один результат.</p></div><span id="counter" class="counter">0</span></div><div id="message" class="message" role="status" aria-live="polite"></div><div id="grid" class="grid"><div class="loading">Загрузка очереди…</div></div></main>
<script>
let token='',items=new Map();const pending=new Set();const grid=document.getElementById('grid'),message=document.getElementById('message'),counter=document.getElementById('counter'),roleLabel=document.getElementById('roleLabel');
function esc(v){return String(v??'').replace(/[&<>\x22\x27]/g,c=>c==='&'?'&amp;':c==='<'?'&lt;':c==='>'?'&gt;':c.charCodeAt(0)===34?'&quot;':'&#39;')}
function feedback(text,state=''){message.textContent=text;message.className='message '+state}
function dt(v){return v?new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short'}).format(new Date(v)):'—'}
function size(v){if(v===null||v===undefined)return '—';if(v<1024)return v+' Б';if(v<1048576)return (v/1024).toFixed(1)+' КБ';return (v/1048576).toFixed(1)+' МБ'}
async function api(path,opts={}){const r=await fetch(path,{...opts,credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});if(r.status===401||r.status===403){location.href='/login';throw new Error('Сессия истекла или недостаточно прав')}const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка запроса');return d}
async function boot(){try{const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();const roles=s.roles||[s.role];if(!roles.some(x=>['admin','superadmin','lawyer'].includes(x))){throw new Error('Требуется роль администратора или юриста')}token=s.api_token||'';roleLabel.textContent=roles.includes('lawyer')&&!roles.includes('admin')?'Документы назначенных вам дел':'Все документы, переданные на юридическую проверку';await load()}catch(e){showError(e)}}
function showError(e){grid.innerHTML=`<div class="error"><b>Не удалось загрузить очередь</b><p>${esc(e.message||e)}</p><button onclick="load()">Повторить</button></div>`;feedback(e.message||String(e),'bad')}
async function load(){grid.innerHTML='<div class="loading">Загрузка очереди…</div>';feedback('');try{const d=await api('/document-access/review/queue');items=new Map((d.items||[]).map(x=>[x.document_id,x]));counter.textContent=d.count||0;if(!d.items?.length){grid.innerHTML='<div class="empty"><b>Очередь пуста</b><p>Все переданные документы уже получили решение.</p><button onclick="load()">Обновить</button></div>';return}grid.innerHTML=d.items.map(card).join('')}catch(e){showError(e)}}
function card(x){return `<article class="card" id="card_${x.document_id}"><div class="card-head"><div><h3>${esc(x.title)}</h3><div>${esc(x.case_number)} · ${esc(x.client_name||'Клиент')}</div></div><span class="badge">На проверке</span></div><div class="meta"><div class="cell"><span>Файл</span>${esc(x.file_name)}</div><div class="cell"><span>Тип</span>${esc(x.document_type)}</div><div class="cell"><span>Версия</span>${esc(x.version)}</div><div class="cell"><span>Размер / загрузка</span>${esc(size(x.file_size))} · ${esc(dt(x.created_at))}</div></div><div class="actions"><button data-document-id="${x.document_id}" onclick="downloadDocument(${x.document_id},this)">Скачать</button><button data-document-id="${x.document_id}" class="green" onclick="openForm(${x.document_id},'approve','Принять документ')">Принять</button><button data-document-id="${x.document_id}" class="amber" onclick="openForm(${x.document_id},'request_reupload','Запросить новую версию')">Новая версия</button><button data-document-id="${x.document_id}" class="red" onclick="openForm(${x.document_id},'reject','Отклонить документ')">Отклонить</button></div><div class="review-form" id="form_${x.document_id}"><h4 id="form_title_${x.document_id}"></h4><textarea id="comment_${x.document_id}" placeholder="Комментарий клиенту"></textarea><div id="hint_${x.document_id}" class="hint"></div><div class="actions"><button data-document-id="${x.document_id}" id="submit_${x.document_id}" onclick="submitDecision(${x.document_id},this)">Подтвердить решение</button><button class="secondary" onclick="closeForm(${x.document_id})">Отмена</button></div></div></article>`}
function controls(id){return Array.from(document.querySelectorAll(`[data-document-id="${id}"]`))}
async function withDocument(id,button,work,label='Выполняется…'){if(pending.has(id))return;pending.add(id);const list=controls(id),labels=new Map(list.filter(x=>x.tagName==='BUTTON').map(x=>[x,x.textContent]));list.forEach(x=>{x.disabled=true;x.setAttribute('aria-busy','true')});if(button)button.textContent=label;try{return await work()}finally{pending.delete(id);list.forEach(x=>{x.disabled=false;x.removeAttribute('aria-busy')});labels.forEach((v,x)=>x.textContent=v)}}
function openForm(id,decision,title){document.querySelectorAll('.review-form').forEach(x=>x.classList.remove('open'));const form=document.getElementById('form_'+id);form.dataset.decision=decision;document.getElementById('form_title_'+id).textContent=title;document.getElementById('comment_'+id).value='';document.getElementById('hint_'+id).textContent=decision==='approve'?'Комментарий необязателен.':'Обязательно укажите понятную причину — минимум 10 символов.';form.classList.add('open');document.getElementById('comment_'+id).focus()}
function closeForm(id){document.getElementById('form_'+id).classList.remove('open')}
async function downloadDocument(id,button){return withDocument(id,button,async()=>{try{const d=await api('/document-access/documents/'+id+'/grant',{method:'POST',body:'{}'});feedback('Одноразовая ссылка подготовлена. Начинается скачивание.','ok');window.location.assign(d.download_url)}catch(e){feedback('Документ не скачан: '+e.message,'bad')}},'Подготовка…')}
async function submitDecision(id,button){const x=items.get(id),form=document.getElementById('form_'+id),decision=form.dataset.decision||'',comment=document.getElementById('comment_'+id).value.trim();if(!x){feedback('Документ уже отсутствует в текущей очереди. Обновите список.','bad');return}if(['request_reupload','reject'].includes(decision)&&comment.length<10){feedback('Укажите причину — минимум 10 символов.','bad');return}const labels={approve:'принять документ',request_reupload:'запросить новую версию',reject:'отклонить документ'};if(!confirm(`Подтвердить решение: ${labels[decision]}? Клиент получит уведомление.`))return;return withDocument(id,button,async()=>{try{const d=await api('/document-access/review/documents/'+id+'/decision',{method:'POST',body:JSON.stringify({decision,comment,expected_status:x.status,expected_version:x.version,expected_updated_at:x.updated_at})});feedback(d.changed?`Решение сохранено: ${d.status_label}`:`Решение уже было сохранено: ${d.status_label}`,'ok');try{await load()}catch(e){feedback('Решение сохранено, но очередь не обновилась: '+e.message,'warn')}}catch(e){feedback('Решение не сохранено: '+e.message,'bad')}},'Сохранение…')}
boot();
</script>
</body>
</html>
"""
