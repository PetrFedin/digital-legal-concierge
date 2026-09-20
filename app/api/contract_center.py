from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.cases.case_responsibility import lawyer_can_access_case
from app.domain.cases.service_contract import (
    current_service_contract,
    publish_service_contract,
)
from app.domain.documents.document_service import DuplicateDocumentError
from app.domain.documents.staff_upload_storage import save_staff_upload
from app.domain.statuses.case_statuses import CaseStatus
from app.models.case import Case
from app.models.user import User
from app.security.document_access import DocumentActor, resolve_document_actor
from app.security.file_uploads import UploadSecurityError
from app.storage import LocalStorageService

router = APIRouter(prefix="/contracts", tags=["service-contracts"])


def _session_token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _actor(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
) -> DocumentActor:
    return await resolve_document_actor(db, _session_token(request, header_token))


async def _case_for_actor(
    db: AsyncSession,
    *,
    case_id: int,
    actor: DocumentActor,
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
    if actor.role == "lawyer" and not await lawyer_can_access_case(
        db,
        case=case,
        lawyer_id=actor.lawyer_id,
    ):
        raise HTTPException(
            status_code=403,
            detail="Дело не назначено текущему юристу",
        )
    return case, user


def _upload_open(case: Case) -> bool:
    return bool(
        str(case.route or "") == "M1"
        and str(case.status) == CaseStatus.M1_CONTRACT_READY.value
    )


@router.get("/cases/{case_id}")
async def contract_context(
    case_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _actor(request, db, x_admin_token)
    case, user = await _case_for_actor(db, case_id=case_id, actor=actor)
    document = await current_service_contract(db, case_id=case.id)
    return {
        "case_id": case.id,
        "case_number": case.case_number,
        "client_name": user.full_name,
        "route": case.route,
        "status": case.status,
        "next_action": case.next_action,
        "actor_role": actor.role,
        "upload_allowed": _upload_open(case),
        "contract": (
            {
                "document_id": int(document.id),
                "title": document.title,
                "file_name": document.file_name,
                "version": int(document.version or 1),
                "file_size": document.file_size,
                "sha256_prefix": str(document.sha256 or "")[:12],
                "updated_at": document.updated_at.isoformat() if document.updated_at else None,
            }
            if document
            else None
        ),
        "materials_url": f"/document-access/ui?case_id={case.id}",
        "messages_url": f"/message-center/ui?case_id={case.id}",
    }


@router.post("/cases/{case_id}/document")
async def upload_service_contract(
    case_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _actor(request, db, x_admin_token)
    case, _ = await _case_for_actor(db, case_id=case_id, actor=actor)
    if not _upload_open(case):
        raise HTTPException(
            status_code=409,
            detail=(
                "Новая версия договора доступна только пока дело находится на этапе подготовки договора. "
                "После подтверждения клиентом договор нельзя незаметно заменить."
            ),
        )

    original_name = str(request.headers.get("x-file-name") or "").strip()
    if not original_name:
        raise HTTPException(status_code=400, detail="Не передано имя файла договора")
    mime_type = str(
        request.headers.get("x-file-type")
        or request.headers.get("content-type")
        or ""
    ).strip() or None
    try:
        declared_size = int(request.headers.get("content-length") or 0) or None
    except ValueError:
        declared_size = None

    stored = None
    try:
        stored = await save_staff_upload(
            chunks=request.stream(),
            case_id=case.id,
            original_name=original_name,
            mime_type=mime_type,
            declared_size=declared_size,
        )
        document = await publish_service_contract(
            db,
            actor=actor,
            case=case,
            stored=stored,
        )
        await db.commit()
        await db.refresh(document)
    except UploadSecurityError as error:
        await db.rollback()
        raise HTTPException(status_code=400, detail=error.user_message) from error
    except DuplicateDocumentError as error:
        await db.rollback()
        if stored is not None:
            LocalStorageService().discard_stored_file(stored.storage_path)
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        await db.rollback()
        if stored is not None:
            LocalStorageService().discard_stored_file(stored.storage_path)
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        if stored is not None:
            try:
                LocalStorageService().discard_stored_file(stored.storage_path)
            except Exception:
                pass
        raise

    return {
        "ok": True,
        "case_id": case.id,
        "document_id": document.id,
        "version": document.version,
        "file_name": document.file_name,
        "sha256_prefix": str(document.sha256 or "")[:12],
    }


CONTRACT_CENTER_HTML = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Договор по делу</title>
<style>
:root{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--blue2:#eef2ff;--green:#14804a;--green2:#ecfdf3;--red:#b42318;--red2:#fef3f2;--amber:#a15c00;--amber2:#fff7e6;--shadow:0 12px 32px rgba(16,24,40,.07)}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif}header{background:linear-gradient(135deg,#111827,#26334f);color:#fff;position:sticky;top:0;z-index:10}.top{max-width:1080px;margin:auto;padding:18px 20px;display:flex;justify-content:space-between;gap:14px;align-items:center}.top h1{font-size:20px;margin:0 0 3px}.top p{margin:0;color:#d0d5dd;font-size:12px}.links{display:flex;gap:8px;flex-wrap:wrap}.links a{color:#fff;text-decoration:none;border:1px solid #ffffff40;border-radius:9px;padding:8px 10px;font-size:13px;font-weight:700}main{max-width:1080px;margin:auto;padding:20px}.hero{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(280px,.7fr);gap:14px}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:17px;box-shadow:var(--shadow)}.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:.08em;font-weight:800;color:var(--muted)}h2,h3{margin:5px 0 8px}.muted{color:var(--muted);font-size:13px;line-height:1.5}.state{padding:12px;border-radius:12px;background:var(--blue2);margin:12px 0}.state.ready{background:var(--green2);color:var(--green)}.state.wait{background:var(--amber2);color:var(--amber)}.contract{border:1px solid var(--line);border-radius:13px;padding:13px;margin-top:12px}.meta{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:9px}.cell{background:#f8fafc;border-radius:10px;padding:9px}.cell span{display:block;color:var(--muted);font-size:11px;margin-bottom:3px}.upload{margin-top:14px;padding-top:14px;border-top:1px solid var(--line)}input[type=file]{display:block;width:100%;padding:11px;background:#fff;border:1px dashed #98a2b3;border-radius:11px;margin:9px 0}button,.button{border:0;border-radius:10px;padding:10px 13px;background:var(--blue);color:#fff;font-weight:750;cursor:pointer;text-decoration:none;display:inline-block}button:focus-visible,.button:focus-visible,.links a:focus-visible,input[type=file]:focus-visible{outline:3px solid #c7d2fe;outline-offset:2px}button:disabled{opacity:.55;cursor:wait}.button.secondary{background:#475467}.actions{display:flex;gap:8px;flex-wrap:wrap}.feedback{min-height:22px;margin-top:10px;font-size:13px}.feedback.bad{color:var(--red)}.feedback.ok{color:var(--green)}.rule{background:var(--amber2);border:1px solid #fedf89;border-radius:13px;padding:13px}.rule b{display:block;margin-bottom:4px}@media(max-width:760px){header{position:static}.top{align-items:flex-start;flex-direction:column}.hero{grid-template-columns:1fr}main{padding:12px}.meta{grid-template-columns:1fr}.actions>*{flex:1;text-align:center}}
</style></head><body>
<header><div class="top"><div><h1>📝 Договор по делу</h1><p>Конкретная версия → проверенный файл → подтверждение клиента → первый платёж</p></div><div class="links"><a id="back" href="/operator">← Кабинет</a><a id="messages" href="#">Переписка</a></div></div></header>
<main><div class="hero"><section class="card"><div class="eyebrow">Текущее дело</div><h2 id="caseTitle">Загрузка…</h2><div id="caseMeta" class="muted"></div><div id="state"></div><div id="contract"></div><div id="upload" class="upload" hidden><h3>Опубликовать новую версию</h3><div class="muted">PDF/DOCX/JPG/PNG. Файл проходит ту же проверку безопасности и envelope-шифрование, что документы клиента. Предыдущая версия останется в истории, но подтверждать клиент сможет только текущую.</div><input id="file" type="file" accept=".pdf,.docx,.jpg,.jpeg,.png"><div class="actions"><button id="uploadButton" onclick="uploadContract()">Проверить и опубликовать</button><a id="materials" class="button secondary" href="#">Все материалы</a></div></div><div id="feedback" class="feedback" role="status"></div></section><aside class="rule"><b>Правило договора</b><div class="muted">Не просите клиента подтверждать договор, пока текущая версия не опубликована. После клиентского подтверждения файл нельзя заменить через этот экран: новая редакция требует отдельного согласованного процесса, а не тихой подмены.</div></aside></div></main>
<script>
const params=new URLSearchParams(location.search),caseId=Number(params.get('case_id')||0);let token='',busy=false,data=null;const $=id=>document.getElementById(id);function e(v){return String(v??'').replace(/[&<>\x22\x27]/g,c=>c==='&'?'&amp;':c==='<'?'&lt;':c==='>'?'&gt;':c.charCodeAt(0)===34?'&quot;':'&#39;')}function size(v){v=Number(v||0);if(!v)return'—';if(v<1024)return v+' Б';if(v<1048576)return(v/1024).toFixed(1)+' КБ';return(v/1048576).toFixed(1)+' МБ'}function say(t,c=''){const x=$('feedback');x.textContent=t;x.className='feedback '+c}async function api(path,opt={}){const r=await fetch(path,{...opt,credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token,...(opt.headers||{})}});const d=await r.json().catch(()=>({}));if(r.status===401||r.status===403){location.href='/login';throw Error('Сессия истекла')}if(!r.ok)throw Error(d.detail||'Ошибка запроса');return d}
function render(){if(!data)return;$('caseTitle').textContent=data.case_number+' · '+data.client_name;$('caseMeta').textContent='Маршрут '+(data.route||'—')+' · статус '+data.status;$('messages').href=data.messages_url;$('materials').href=data.materials_url;$('back').href=data.actor_role==='lawyer'?'/lawyer/workspace/ui?case_id='+data.case_id:'/admin/workdesk/ui';$('upload').hidden=!data.upload_allowed;const c=data.contract;if(c){$('state').innerHTML='<div class="state ready"><b>✓ Текущая версия опубликована</b><br>Клиент может открыть именно эту версию в Telegram и подтвердить её.</div>';$('contract').innerHTML=`<div class="contract"><b>${e(c.title)} · версия ${c.version}</b><div class="muted">${e(c.file_name)}</div><div class="meta"><div class="cell"><span>Размер</span>${e(size(c.file_size))}</div><div class="cell"><span>Контроль SHA-256</span>${e(c.sha256_prefix)}…</div></div></div>`}else{$('state').innerHTML=`<div class="state wait"><b>Договор ещё не опубликован</b><br>Клиент не сможет открыть 30 000 ₽, пока нет проверенной версии файла.</div>`;$('contract').innerHTML=''}if(!data.upload_allowed&&data.status!=='M1_CONTRACT_READY')say('Этап договора уже завершён или изменился. Новую версию через этот экран публиковать нельзя.')}
async function load(){if(!caseId){throw Error('Не указан case_id')}data=await api('/contracts/cases/'+caseId);render()}
async function uploadContract(){if(busy)return;const file=$('file').files?.[0];if(!file){say('Выберите файл договора.','bad');return}if(!confirm('Опубликовать выбранный файл как текущую версию договора для клиента?'))return;busy=true;const b=$('uploadButton'),old=b.textContent;b.disabled=true;b.textContent='Проверка и шифрование…';try{await api('/contracts/cases/'+caseId+'/document',{method:'POST',body:file,headers:{'Content-Type':file.type||'application/octet-stream','x-file-name':file.name,'x-file-type':file.type||''}});say('Новая версия договора опубликована и доступна клиенту.','ok');$('file').value='';await load()}catch(err){say('Договор не опубликован: '+err.message,'bad')}finally{busy=false;b.disabled=false;b.textContent=old}}
async function boot(){const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();const roles=s.roles||[s.role];if(!roles.some(x=>['lawyer','admin','superadmin'].includes(x))){location.href='/operator';return}token=s.api_token||'';try{await load()}catch(err){say(err.message,'bad')}}boot();
</script></body></html>
"""


@router.get("/ui", response_class=HTMLResponse)
async def contract_center_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        await _actor(request, db, x_admin_token)
    except HTTPException:
        await db.rollback()
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(CONTRACT_CENTER_HTML)