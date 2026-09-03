from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import actor_id_from_token, require_admin
from app.api.workdesk_ui import WORKDESK_HTML
from app.config import settings
from app.db.session import get_db
from app.domain.cases.case_responsibility import effective_lawyer_ids_for_cases
from app.domain.cases.case_timeline import get_client_visible_status
from app.domain.cases.error_recovery_service import (
    CaseErrorRecoveryError,
    CaseErrorRecoveryService,
)
from app.domain.statuses.case_statuses import CaseStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.lawyer import Lawyer
from app.models.message import Message
from app.models.payment import Payment
from app.models.user import User

router = APIRouter(tags=["technical-case-recovery"])


async def _error_rows(db: AsyncSession, *, limit: int = 100):
    rows = list(
        (
            await db.execute(
                select(Case, User)
                .join(User, User.id == Case.client_id)
                .where(Case.status == CaseStatus.ERROR.value)
                .order_by(Case.updated_at.asc(), Case.id.asc())
                .limit(min(max(int(limit), 1), 300))
            )
        ).all()
    )
    if not rows:
        return []

    cases = [case for case, _user in rows]
    responsibilities = await effective_lawyer_ids_for_cases(db, cases)
    lawyer_ids = sorted(
        {
            int(value)
            for value in responsibilities.values()
            if value is not None
        }
    )
    lawyers = {}
    if lawyer_ids:
        lawyers = {
            int(item.id): item.full_name
            for item in (
                await db.execute(select(Lawyer).where(Lawyer.id.in_(lawyer_ids)))
            ).scalars().all()
        }

    result = []
    for case, user in rows:
        lawyer_id = responsibilities.get(int(case.id))
        result.append(
            {
                "case_id": int(case.id),
                "case_number": case.case_number,
                "client_name": user.full_name,
                "route": case.route,
                "route_label": (
                    "Ведение дела"
                    if str(case.route or "") == "M1"
                    else "Консультация"
                    if str(case.route or "") == "M2"
                    else "Маршрут не определён"
                ),
                "status": str(case.status),
                "status_label": get_client_visible_status(case.status),
                "lawyer_id": lawyer_id,
                "lawyer_name": lawyers.get(int(lawyer_id)) if lawyer_id else None,
                "updated_at": case.updated_at.isoformat(),
                "next_action": (
                    "Техническая проверка: восстановить только подтверждённый аудитом этап"
                ),
            }
        )
    return result


@router.get("/admin/technical-cases")
async def technical_cases(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    items = await _error_rows(db)
    return {
        "count": len(items),
        "items": items,
    }


async def _count(db: AsyncSession, model, case_id: int) -> int:
    return int(
        (
            await db.execute(
                select(func.count()).select_from(model).where(model.case_id == case_id)
            )
        ).scalar_one()
        or 0
    )


@router.get("/admin/technical-cases/{case_id}/context")
async def technical_case_context(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    row = (
        await db.execute(
            select(Case, User)
            .join(User, User.id == Case.client_id)
            .where(Case.id == int(case_id))
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Дело не найдено")
    case, user = row
    if str(case.status) != CaseStatus.ERROR.value:
        raise HTTPException(
            status_code=409,
            detail="Дело уже вышло из технического статуса. Вернитесь в рабочий стол.",
        )

    lawyer_map = await effective_lawyer_ids_for_cases(db, [case])
    lawyer_id = lawyer_map.get(int(case.id))
    lawyer = await db.get(Lawyer, lawyer_id) if lawyer_id else None
    suggestion = await CaseErrorRecoveryService(db).suggest(case.id)

    return {
        "case": {
            "id": int(case.id),
            "number": case.case_number,
            "route": case.route,
            "route_label": (
                "Ведение дела"
                if str(case.route or "") == "M1"
                else "Консультация"
                if str(case.route or "") == "M2"
                else "Маршрут не определён"
            ),
            "status": str(case.status),
            "status_label": get_client_visible_status(case.status),
            "next_action": case.next_action,
            "updated_at": case.updated_at.isoformat(),
            "internal_comment": str(case.internal_comment or "").strip() or None,
        },
        "client": {
            "name": user.full_name,
        },
        "responsibility": {
            "lawyer_id": lawyer_id,
            "lawyer_name": lawyer.full_name if lawyer else None,
        },
        "counts": {
            "documents": await _count(db, Document, case.id),
            "messages": await _count(db, Message, case.id),
            "payments": await _count(db, Payment, case.id),
            "consultations": await _count(db, Consultation, case.id),
        },
        "recovery": (
            {
                "available": True,
                "target_status": suggestion.status.value,
                "target_status_label": get_client_visible_status(suggestion.status),
                "target_route": suggestion.route,
                "audit_event_id": suggestion.audit_event_id,
                "source": suggestion.source,
                "occurred_at": (
                    suggestion.occurred_at.isoformat()
                    if suggestion.occurred_at
                    else None
                ),
            }
            if suggestion is not None
            else {
                "available": False,
                "reason": (
                    "Последний безопасный M1/M2-этап не подтверждён переходом в аудите "
                    "или относится к платёжному/записному контуру. Статус нельзя угадывать."
                ),
            }
        ),
    }


@router.post("/admin/technical-cases/{case_id}/recover")
async def recover_technical_case(
    case_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = require_admin(x_admin_token)
    try:
        case, suggestion = await CaseErrorRecoveryService(db).recover_last_safe_status(
            case_id=case_id,
            actor_id=actor_id_from_token(actor),
            expected_updated_at=payload.get("expected_updated_at"),
            comment=payload.get("comment") or "",
        )
        await db.commit()
        await db.refresh(case)
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except CaseErrorRecoveryError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise
    return {
        "ok": True,
        "case_id": case.id,
        "status": str(case.status),
        "status_label": get_client_visible_status(case.status),
        "route": case.route,
        "next_action": case.next_action,
        "source_audit_event_id": suggestion.audit_event_id,
        "updated_at": case.updated_at.isoformat(),
    }


_TECHNICAL_WORKDESK_PATCH = r"""
<script>
(function(){
  const host=document.getElementById('technicalCasesBanner');
  if(!host)return;
  const escTech=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function loadTechnical(){
    try{
      const sessionResponse=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});
      if(!sessionResponse.ok)return;
      const session=await sessionResponse.json();
      if(!(session.roles||[session.role]).includes('admin'))return;
      const response=await fetch('/admin/technical-cases',{credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':session.api_token||''}});
      if(!response.ok)return;
      const data=await response.json();
      const rows=data.items||[];
      if(!rows.length){host.innerHTML='';return}
      const preview=rows.slice(0,3).map(x=>`<a href="/admin/technical-cases/ui?case_id=${Number(x.case_id)}" style="display:flex;justify-content:space-between;gap:10px;padding:9px 0;border-top:1px solid #fecdca;text-decoration:none;color:#7a271a"><span><b>${escTech(x.case_number)}</b> · ${escTech(x.client_name)}</span><span>${escTech(x.route_label)}</span></a>`).join('');
      host.innerHTML=`<section style="margin-bottom:14px;background:#fef3f2;border:1px solid #fecdca;border-radius:14px;padding:14px;color:#7a271a"><div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap"><div><b style="font-size:16px">🛠 Техническая очередь: ${rows.length}</b><div style="font-size:13px;margin-top:4px">Эти дела остановлены fail-closed. Оплата, запись и переходы из старых кнопок заблокированы до проверки.</div></div><a class="button" style="background:#b42318" href="/admin/technical-cases/ui?case_id=${Number(rows[0].case_id)}">Разобрать первое дело</a></div>${preview}${rows.length>3?`<div style="font-size:12px;margin-top:7px">Ещё: ${rows.length-3}</div>`:''}</section>`;
    }catch(_){/* Основной workdesk остаётся доступным даже если технический баннер не загрузился. */}
  }
  loadTechnical();
})();
</script>
"""


def _guided_workdesk_html() -> str:
    html = WORKDESK_HTML
    marker = "<main>"
    if html.count(marker) != 1:
        raise RuntimeError("Workdesk template contract changed: <main> marker")
    html = html.replace(marker, '<main><div id="technicalCasesBanner"></div>', 1)
    end = "</body>"
    if html.count(end) != 1:
        raise RuntimeError("Workdesk template contract changed: </body> marker")
    return html.replace(end, _TECHNICAL_WORKDESK_PATCH + end, 1)


@router.get("/admin/workdesk/ui", response_class=HTMLResponse)
async def guided_workdesk_ui(request: Request):
    token = request.headers.get("x-admin-token") or request.cookies.get(
        settings.admin_session_cookie
    )
    if not token:
        return RedirectResponse(url="/login", status_code=303)
    require_admin(token)
    return HTMLResponse(_guided_workdesk_html())


@router.get("/admin/technical-cases/ui", response_class=HTMLResponse)
async def technical_case_ui(request: Request):
    token = request.headers.get("x-admin-token") or request.cookies.get(
        settings.admin_session_cookie
    )
    if not token:
        return RedirectResponse(
            url="/login?next=/admin/technical-cases/ui",
            status_code=303,
        )
    require_admin(token)
    return HTMLResponse(TECHNICAL_CASE_HTML)


TECHNICAL_CASE_HTML = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Техническая карточка дела</title>
<style>
:root{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--red:#b42318;--red2:#fef3f2;--amber:#a15c00;--amber2:#fff7e6;--green:#14804a;--green2:#ecfdf3}*{box-sizing:border-box}body{margin:0;background:var(--bg);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:var(--ink)}header{background:linear-gradient(135deg,#111827,#3a2630);color:white;padding:18px 22px}header .inner{max-width:1000px;margin:auto;display:flex;justify-content:space-between;gap:12px;align-items:center}h1{font-size:21px;margin:0 0 4px}header p{margin:0;color:#d0d5dd;font-size:13px}main{max-width:1000px;margin:auto;padding:20px}.card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:17px;margin-bottom:12px}.danger{background:var(--red2);border-color:#fecdca}.safe{background:var(--green2);border-color:#abefc6}.warn{background:var(--amber2);border-color:#fedf89}.head,.row{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap}.badge{display:inline-flex;padding:5px 9px;border-radius:999px;background:#fee4e2;color:var(--red);font-size:12px;font-weight:800}.muted{color:var(--muted);font-size:13px;line-height:1.5}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px;margin-top:12px}.cell{background:#f8fafc;border-radius:10px;padding:10px}.cell span{display:block;color:var(--muted);font-size:11px;margin-bottom:4px}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.button,button{border:0;border-radius:10px;padding:10px 12px;background:var(--blue);color:#fff;text-decoration:none;font:inherit;font-weight:750;cursor:pointer}.secondary{background:#475467!important}.red{background:var(--red)!important}.green{background:var(--green)!important}textarea{width:100%;min-height:100px;border:1px solid #d0d5dd;border-radius:11px;padding:11px;font:inherit;margin:10px 0}textarea:focus{outline:0;border-color:#9db0f5;box-shadow:0 0 0 3px #eef2ff}button:disabled{opacity:.55;cursor:wait}.feedback{min-height:22px;font-size:13px;margin:8px 0}.feedback.bad{color:var(--red)}.feedback.ok{color:var(--green)}.review{display:none;background:#f8fafc;border:1px solid var(--line);border-radius:11px;padding:12px;margin-top:10px}.review.open{display:block}.timeline{display:grid;gap:10px}.event{border-left:3px solid #c7d2fe;padding:2px 0 2px 12px}.event b{display:block}.event small{color:var(--muted)}.empty{padding:22px;text-align:center;color:var(--muted)}@media(max-width:700px){header .inner{align-items:flex-start;flex-direction:column}.grid{grid-template-columns:1fr 1fr}main{padding:12px}.actions .button,.actions button{flex:1;text-align:center}}@media(max-width:430px){.grid{grid-template-columns:1fr}.actions{flex-direction:column}.actions .button,.actions button{width:100%}}
</style></head><body>
<header><div class="inner"><div><h1>🛠 Техническая карточка дела</h1><p>Ошибка не является бизнес-этапом: сначала контекст, затем одно безопасное восстановление.</p></div><a class="button secondary" href="/admin/workdesk/ui">← Рабочий стол</a></div></header>
<main><div id="feedback" class="feedback" role="status"></div><div id="content"><div class="card">Загрузка…</div></div></main>
<script>
let token='',data=null,caseId=Number(new URLSearchParams(location.search).get('case_id')||0);const content=document.getElementById('content'),feedback=document.getElementById('feedback');
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function dt(v){if(!v)return'—';try{return new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short'}).format(new Date(v))}catch{return String(v)}}function say(t,c=''){feedback.textContent=t;feedback.className='feedback '+c}async function api(path,opt={}){const r=await fetch(path,{...opt,credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token,'Content-Type':'application/json',...(opt.headers||{})}});const d=await r.json().catch(()=>({}));if(!r.ok){const e=new Error(d.detail||'Ошибка запроса');e.status=r.status;throw e}return d}
function links(){return `<div class="actions"><a class="button" href="/message-center/ui?case_id=${caseId}">Переписка</a><a class="button secondary" href="/document-access/ui?case_id=${caseId}">Материалы</a><a class="button secondary" href="/diagnostic-center/ui">Диагностика системы</a><a class="button secondary" href="/admin/workdesk/ui">Рабочий стол</a></div>`}
function render(){const c=data.case,r=data.recovery,res=data.responsibility||{},counts=data.counts||{};const recovery=r.available?`<section class="card safe"><div class="head"><div><b>Безопасное восстановление найдено</b><div class="muted">Аудит подтверждает последний case-owned этап. Платёжные и записные состояния здесь никогда не угадываются.</div></div><span class="badge" style="background:#d1fadf;color:#067647">${esc(r.target_status_label)}</span></div><div class="grid"><div class="cell"><span>Целевой статус</span><b>${esc(r.target_status)}</b></div><div class="cell"><span>Маршрут</span><b>${esc(r.target_route)}</b></div><div class="cell"><span>Аудит-основание</span><b>#${esc(r.audit_event_id)}</b></div><div class="cell"><span>Зафиксировано</span><b>${esc(dt(r.occurred_at))}</b></div></div><div id="editRecovery"><label><b>Причина восстановления</b></label><textarea id="comment" placeholder="Что проверено и почему возврат на предыдущий этап безопасен"></textarea><div class="muted">Минимум 10 символов. Система сама выбрала целевой статус из аудита; вручную выбрать другой нельзя.</div><div class="actions"><button onclick="reviewRecovery()">Проверить восстановление</button></div></div><div id="review" class="review"><b>Что изменится</b><p>Дело выйдет из ERROR и вернётся только в подтверждённый статус <b>${esc(r.target_status_label)}</b>. Маршрут станет ${esc(r.target_route)}. Платежи, документы, консультации и переписка не переписываются.</p><div class="muted" id="reviewComment"></div><div class="actions"><button id="confirm" class="green" onclick="recover(this)">Подтвердить восстановление</button><button class="secondary" onclick="editRecovery()">← Изменить комментарий</button></div></div></section>`:`<section class="card warn"><b>Автоматическое восстановление не предлагается</b><p class="muted">${esc(r.reason||'Безопасный этап не подтверждён.')}</p><div class="muted">Проверьте временную шкалу, платёжный/консультационный контур и диагностику. Произвольное изменение ERROR через общий переключатель статуса заблокировано.</div>${links()}</section>`;content.innerHTML=`<section class="card danger"><div class="head"><div><b style="font-size:18px">${esc(c.number)} · ${esc(data.client?.name||'Клиент')}</b><div class="muted">${esc(c.route_label)} · обновлено ${esc(dt(c.updated_at))}</div></div><span class="badge">Техническая блокировка</span></div><p><b>Бизнес-действия остановлены.</b> Старые клиентские кнопки не могут запустить оплату, запись или переход, пока статус ERROR не разобран.</p><div class="grid"><div class="cell"><span>Статус</span><b>${esc(c.status_label)}</b></div><div class="cell"><span>Ответственный</span><b>${esc(res.lawyer_name||'не определён')}</b></div><div class="cell"><span>Документы / сообщения</span><b>${Number(counts.documents||0)} / ${Number(counts.messages||0)}</b></div><div class="cell"><span>Платежи / консультации</span><b>${Number(counts.payments||0)} / ${Number(counts.consultations||0)}</b></div></div>${c.internal_comment?`<p class="muted"><b>Внутренний комментарий:</b> ${esc(c.internal_comment)}</p>`:''}${links()}</section>${recovery}<section class="card"><div class="head"><b>Последние события дела</b><button class="secondary" onclick="loadTimeline(this)">Обновить</button></div><div id="timeline" class="timeline"><div class="empty">Загрузка истории…</div></div></section>`;loadTimeline()}
function reviewRecovery(){const value=(document.getElementById('comment')?.value||'').trim();if(value.length<10){say('Опишите причину восстановления минимум в 10 символах.','bad');return}document.getElementById('reviewComment').textContent='Комментарий: '+value;document.getElementById('editRecovery').style.display='none';document.getElementById('review').classList.add('open');say('Проверьте целевой этап и подтвердите действие.','')}
function editRecovery(){document.getElementById('editRecovery').style.display='block';document.getElementById('review').classList.remove('open');document.getElementById('comment')?.focus()}
async function recover(button){const comment=(document.getElementById('comment')?.value||'').trim();if(comment.length<10){editRecovery();say('Комментарий слишком короткий.','bad');return}button.disabled=true;try{const result=await api(`/admin/technical-cases/${caseId}/recover`,{method:'POST',body:JSON.stringify({expected_updated_at:data.case.updated_at,comment})});content.innerHTML=`<section class="card safe"><b style="font-size:18px">✅ Дело восстановлено</b><p>Новый статус: <b>${esc(result.status_label)}</b>. Следующее действие: ${esc(result.next_action||'проверьте рабочий стол')}.</p><div class="muted">Восстановление привязано к аудиту #${esc(result.source_audit_event_id)}. Данные платежей, консультаций и документов не переписывались.</div>${links()}</section>`;say('Техническая блокировка снята.','ok')}catch(e){say(e.message,'bad');if(e.status===409){button.disabled=false;document.getElementById('review')?.classList.remove('open');document.getElementById('editRecovery')&&(document.getElementById('editRecovery').style.display='block')}}}
async function loadTimeline(button=null){if(button)button.disabled=true;const host=document.getElementById('timeline');if(!host)return;try{const d=await api(`/admin/workdesk/cases/${caseId}/timeline?limit=12`);host.innerHTML=(d.items||[]).length?(d.items||[]).map(x=>`<div class="event"><b>${esc(x.title)}</b><div>${esc(x.detail||'')}</div><small>${esc(x.actor_label)} · ${esc(dt(x.occurred_at))}</small></div>`).join(''):'<div class="empty">Событий для отображения пока нет.</div>'}catch(e){host.innerHTML=`<div class="empty">История не загружена: ${esc(e.message)}</div>`}finally{if(button)button.disabled=false}}
async function boot(){if(!caseId){content.innerHTML='<section class="card danger"><b>Не указан ID дела</b><p class="muted">Откройте техническое дело из рабочего стола.</p><a class="button" href="/admin/workdesk/ui">Вернуться</a></section>';return}try{const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login?next='+encodeURIComponent(location.pathname+location.search);return}const s=await r.json();if(!(s.roles||[s.role]).includes('admin'))throw new Error('Требуется роль администратора');token=s.api_token||'';data=await api(`/admin/technical-cases/${caseId}/context`);render()}catch(e){content.innerHTML=`<section class="card danger"><b>Техническая карточка не открыта</b><p class="muted">${esc(e.message)}</p><a class="button" href="/admin/workdesk/ui">Вернуться в рабочий стол</a></section>`;say(e.message,'bad')}}boot();
</script></body></html>
"""
