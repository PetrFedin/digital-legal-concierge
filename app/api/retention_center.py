from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.domain.retention.case_retention_service import (
    CaseRetentionError,
    CaseRetentionService,
)
from app.models.case import Case
from app.models.case_retention import CaseRetentionRecord
from app.security.access_control import ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(prefix="/retention", tags=["retention"])


class ScanPayload(BaseModel):
    persist: bool = False


class ReasonPayload(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)


class ApprovalPayload(BaseModel):
    comment: str = Field(min_length=10, max_length=2000)


async def require_retention_superadmin(
    db: AsyncSession,
    token: str | None,
):
    try:
        actor = await resolve_document_actor(db, token)
    except DocumentAccessError as error:
        raise HTTPException(error.status_code, error.detail) from error
    if actor.role != ROLE_SUPERADMIN:
        raise HTTPException(
            403,
            "Retention Center доступен только персональному суперадминистратору с MFA",
        )
    return actor


def _record_payload(record: CaseRetentionRecord, case: Case) -> dict:
    return {
        "id": record.id,
        "case_id": case.id,
        "case_number": case.case_number,
        "case_status": case.status,
        "closed_at": case.closed_at.isoformat() if case.closed_at else None,
        "content_deleted_at": (
            case.content_deleted_at.isoformat() if case.content_deleted_at else None
        ),
        "status": record.status,
        "policy_version": record.policy_version,
        "retention_due_at": record.retention_due_at.isoformat(),
        "legal_hold": bool(record.legal_hold),
        "legal_hold_reason": record.legal_hold_reason,
        "requested_at": record.requested_at.isoformat() if record.requested_at else None,
        "requested_by": record.requested_by,
        "approved_at": record.approved_at.isoformat() if record.approved_at else None,
        "approved_by": record.approved_by,
        "execution_started_at": (
            record.execution_started_at.isoformat()
            if record.execution_started_at
            else None
        ),
        "executed_at": record.executed_at.isoformat() if record.executed_at else None,
        "attempt_count": record.attempt_count,
        "last_error": record.last_error,
        "documents_deleted": record.documents_deleted,
        "messages_deleted": record.messages_deleted,
        "notifications_deleted": record.notifications_deleted,
        "consultations_anonymized": record.consultations_anonymized,
        "content_digest": record.content_digest,
    }


@router.get("/status")
async def retention_status(
    limit: int = Query(default=200, ge=1, le=500),
    x_admin_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    await require_retention_superadmin(db, x_admin_token)
    bounded_limit = int(limit)
    rows = (
        await db.execute(
            select(CaseRetentionRecord, Case)
            .join(Case, Case.id == CaseRetentionRecord.case_id)
            .order_by(
                CaseRetentionRecord.legal_hold.desc(),
                CaseRetentionRecord.retention_due_at.asc(),
                CaseRetentionRecord.id.asc(),
            )
            .limit(bounded_limit)
        )
    ).all()
    grouped_counts = (
        await db.execute(
            select(CaseRetentionRecord.status, func.count(CaseRetentionRecord.id))
            .group_by(CaseRetentionRecord.status)
        )
    ).all()
    legal_hold_count = (
        await db.execute(
            select(func.count(CaseRetentionRecord.id)).where(
                CaseRetentionRecord.legal_hold.is_(True)
            )
        )
    ).scalar_one()
    return {
        "counts": {str(status): int(count) for status, count in grouped_counts},
        "legal_hold_count": int(legal_hold_count),
        "records": [_record_payload(record, case) for record, case in rows],
    }


@router.post("/scan")
async def scan_retention(
    payload: ScanPayload,
    x_admin_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    await require_retention_superadmin(db, x_admin_token)
    try:
        result = await CaseRetentionService(db).discover_due_cases(
            persist=payload.persist
        )
        await db.commit()
        return result
    except CaseRetentionError as error:
        await db.rollback()
        raise HTTPException(409, str(error)) from error
    except Exception:
        await db.rollback()
        raise


async def _run_and_commit(db: AsyncSession, operation):
    try:
        result = await operation
        await db.commit()
        return result
    except CaseRetentionError as error:
        await db.rollback()
        raise HTTPException(409, str(error)) from error
    except Exception:
        await db.rollback()
        raise


@router.post("/cases/{case_id}/hold")
async def set_hold(
    case_id: int,
    payload: ReasonPayload,
    x_admin_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    actor = await require_retention_superadmin(db, x_admin_token)
    record = await _run_and_commit(
        db,
        CaseRetentionService(db).set_legal_hold(
            case_id=case_id,
            actor_id=actor.account_id,
            reason=payload.reason,
        ),
    )
    return {"ok": True, "record_id": record.id, "legal_hold": True}


@router.post("/cases/{case_id}/hold/release")
async def release_hold(
    case_id: int,
    payload: ReasonPayload,
    x_admin_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    actor = await require_retention_superadmin(db, x_admin_token)
    record = await _run_and_commit(
        db,
        CaseRetentionService(db).release_legal_hold(
            case_id=case_id,
            actor_id=actor.account_id,
            reason=payload.reason,
        ),
    )
    return {"ok": True, "record_id": record.id, "legal_hold": False}


@router.post("/cases/{case_id}/request")
async def request_deletion(
    case_id: int,
    payload: ReasonPayload,
    x_admin_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    actor = await require_retention_superadmin(db, x_admin_token)
    record = await _run_and_commit(
        db,
        CaseRetentionService(db).request_deletion(
            case_id=case_id,
            actor_id=actor.account_id,
            reason=payload.reason,
        ),
    )
    return {"ok": True, "record_id": record.id, "status": record.status}


@router.post("/records/{record_id}/approve")
async def approve_deletion(
    record_id: int,
    payload: ApprovalPayload,
    x_admin_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    actor = await require_retention_superadmin(db, x_admin_token)
    record = await _run_and_commit(
        db,
        CaseRetentionService(db).approve_deletion(
            record_id=record_id,
            actor_id=actor.account_id,
            comment=payload.comment,
        ),
    )
    return {"ok": True, "record_id": record.id, "status": record.status}


@router.post("/records/{record_id}/execute")
async def execute_deletion(
    record_id: int,
    x_admin_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    actor = await require_retention_superadmin(db, x_admin_token)
    try:
        record = await CaseRetentionService(db).execute_deletion(
            record_id=record_id,
            actor_id=actor.account_id,
        )
    except CaseRetentionError as error:
        await db.rollback()
        raise HTTPException(409, str(error)) from error
    except Exception:
        await db.rollback()
        raise
    return {
        "ok": True,
        "record_id": record.id,
        "status": record.status,
        "content_digest": record.content_digest,
        "documents_deleted": record.documents_deleted,
        "messages_deleted": record.messages_deleted,
        "notifications_deleted": record.notifications_deleted,
        "consultations_anonymized": record.consultations_anonymized,
    }


@router.get("/ui", response_class=HTMLResponse)
async def retention_ui():
    return HTMLResponse(RETENTION_HTML)


RETENTION_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Хранение данных</title>
<style>
body{margin:0;background:#f4f6f8;color:#17202a;font-family:Inter,Arial,sans-serif}header{background:#16212d;color:white;padding:18px 24px;display:flex;justify-content:space-between;align-items:center;gap:14px}main{max-width:1280px;margin:24px auto;padding:0 18px}.card{background:white;border-radius:14px;padding:18px;margin-bottom:16px;box-shadow:0 3px 14px #0001}button{border:0;border-radius:8px;padding:9px 12px;cursor:pointer;margin:2px}button:focus-visible,a:focus-visible{outline:3px solid #c7d2fe;outline-offset:2px}button:disabled{opacity:.55;cursor:wait}.primary{background:#1769aa;color:white}.danger{background:#b42318;color:white}.warn{background:#f2a900}.secondary{background:#e8edf2}table{width:100%;border-collapse:collapse}th,td{text-align:left;border-bottom:1px solid #e5e7eb;padding:9px;vertical-align:top}.badge{display:inline-block;padding:3px 7px;border-radius:9px;background:#edf2f7;font-size:12px}.hold{background:#fff1c7}.bad{color:#b42318}.ok{color:#166534}.warning{color:#a16207}.muted{color:#667085;font-size:13px}code{font-size:12px}.actions{display:flex;gap:4px;flex-wrap:wrap}@media(max-width:900px){header{align-items:flex-start;flex-direction:column}header a{display:block;width:100%;text-align:center;padding:8px 10px;border:1px solid #475467;border-radius:9px}table{display:block;overflow-x:auto;font-size:12px}}
</style>
</head>
<body>
<header><b>⚖ Хранение данных</b><div><a href="/operator" style="color:white">Руководство и контроль</a></div></header>
<main>
<div class="card">
<h2>Контролируемое хранение закрытых дел</h2>
<p>Удаляется содержимое дела и зашифрованные файлы. Платёжный ledger, номер дела и неизменяемый аудит сохраняются. Физическое перезаписывание блоков SSD не заявляется.</p>
<button data-scan-action class="secondary" onclick="scan(false,this)">Проверить без изменений</button>
<button data-scan-action class="primary" onclick="scan(true,this)">Добавить просроченные дела в очередь</button>
<span id="summary" class="muted"></span>
<div id="message" class="muted" role="status" aria-live="polite"></div>
</div>
<div class="card" id="records">Загрузка…</div>
</main>
<script>
let token='';let scanPending=false;const pendingCases=new Set();
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function feedback(text,state='muted'){message.textContent=text;message.className=state}
async function api(path,opt={}){const r=await fetch(path,{...opt,credentials:'same-origin',cache:'no-store',headers:{...(opt.headers||{}),'x-admin-token':token,'content-type':'application/json'}});const data=await r.json().catch(()=>({}));if(!r.ok)throw new Error(data.detail||'Ошибка');return data}
async function boot(){const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();if(!(s.roles||[s.role]).includes('superadmin')||!s.mfa_verified){document.body.innerHTML='<main><div class="card"><h2>Недостаточно прав</h2><p>Нужна персональная MFA-сессия суперадминистратора.</p></div></main>';return}token=s.api_token;try{await load()}catch(e){feedback(`Раздел хранения данных не загружен: ${e.message}`,'bad')}}
async function load(){const d=await api('/retention/status');summary.textContent=' Hold: '+d.legal_hold_count+' · '+Object.entries(d.counts).map(x=>x.join(': ')).join(' · ');records.innerHTML='<table><tr><th>Дело</th><th>Состояние</th><th>Срок</th><th>Контроль</th><th>Действия</th></tr>'+d.records.map(row=>`<tr class="${row.legal_hold?'hold':''}"><td><b>${esc(row.case_number)}</b><br><span class="muted">#${row.case_id} · ${esc(row.case_status)}</span></td><td><span class="badge">${esc(row.status)}</span>${row.last_error?'<br><span class="bad">'+esc(row.last_error)+'</span>':''}<br><span class="muted">Попыток: ${row.attempt_count}</span></td><td>${esc(row.retention_due_at)}<br><span class="muted">Закрыто: ${esc(row.closed_at)}</span></td><td>${row.legal_hold?'LEGAL HOLD<br>'+esc(row.legal_hold_reason):'Нет hold'}<br><span class="muted">request: ${esc(row.requested_by)} / approve: ${esc(row.approved_by)}</span></td><td><div class="actions">${actions(row)}</div></td></tr>`).join('')+'</table>'}
function actionButton(caseId,label,css,handler,extra=''){return `<button data-case-id="${caseId}" ${extra} class="${css}" onclick="${handler}">${label}</button>`}
function actions(r){if(r.status==='COMPLETED')return '<code>'+esc(r.content_digest||'completed')+'</code>';if(r.status==='EXECUTING')return '<span class="warning">Удаление выполняется другим процессом</span>';if(r.legal_hold)return actionButton(r.case_id,'Снять hold','secondary',`releaseHold(${r.case_id},this)`);let result=actionButton(r.case_id,'Legal hold','warn',`hold(${r.case_id},this)`);if(['DISCOVERED','FAILED'].includes(r.status))result+=actionButton(r.case_id,'Запросить','secondary',`requestDelete(${r.case_id},this)`);if(r.status==='REQUESTED')result+=actionButton(r.case_id,'Одобрить другим аккаунтом','primary',`approve(${r.id},${r.case_id},this)`);if(['APPROVED','FAILED'].includes(r.status)&&r.approved_at)result+=actionButton(r.case_id,'Удалить содержимое','danger',`executeDelete(${r.id},${r.case_id},this)`,`data-case-number="${esc(r.case_number)}"`);return result}
function validReason(value,label){if(!value)return false;if(value.trim().length<10){feedback(`${label} должен содержать не менее 10 символов`,'bad');return false}return true}
async function refreshAfter(success){try{await load();feedback(success,'ok')}catch(e){feedback(`${success}. Изменение сохранено, но список не обновился: ${e.message}`,'warning')}}
async function withScanAction(button,work){if(scanPending)return;scanPending=true;const controls=Array.from(document.querySelectorAll('[data-scan-action]'));const labels=new Map(controls.map(x=>[x,x.textContent]));controls.forEach(x=>{x.disabled=true;x.setAttribute('aria-busy','true')});if(button)button.textContent='Выполняется…';try{return await work()}finally{scanPending=false;controls.forEach(x=>{x.disabled=false;x.removeAttribute('aria-busy')});labels.forEach((label,x)=>{x.textContent=label})}}
async function withCaseAction(caseId,button,work){if(pendingCases.has(caseId))return;pendingCases.add(caseId);const controls=Array.from(document.querySelectorAll(`[data-case-id="${caseId}"]`));const labels=new Map(controls.map(x=>[x,x.textContent]));controls.forEach(x=>{x.disabled=true;x.setAttribute('aria-busy','true')});if(button)button.textContent='Выполняется…';try{return await work()}finally{pendingCases.delete(caseId);controls.forEach(x=>{x.disabled=false;x.removeAttribute('aria-busy')});labels.forEach((label,x)=>{x.textContent=label})}}
async function scan(persist,button){if(persist&&!confirm('Добавить все найденные просроченные закрытые дела в контролируемую очередь удаления? Содержимое на этом этапе ещё не удаляется.'))return;return withScanAction(button,async()=>{try{const result=await api('/retention/scan',{method:'POST',body:JSON.stringify({persist})});const text=result.dry_run?`Dry run: найдено ${result.due_count}, уже отслеживается ${result.already_tracked}`:`Очередь обновлена: добавлено ${result.created}, уже отслеживается ${result.already_tracked}`;await refreshAfter(text)}catch(e){feedback(`Сканирование не выполнено: ${e.message}`,'bad')}})}
async function hold(caseId,button){const reason=prompt('Причина legal hold (не менее 10 символов):');if(!validReason(reason,'Причина legal hold'))return;if(!confirm('Установить legal hold? Все ранее созданные запросы и одобрения удаления для этого дела будут сброшены.'))return;return withCaseAction(caseId,button,async()=>{try{await api('/retention/cases/'+caseId+'/hold',{method:'POST',body:JSON.stringify({reason})});await refreshAfter(`Legal hold по делу #${caseId} установлен`)}catch(e){feedback(`Legal hold по делу #${caseId} не установлен: ${e.message}`,'bad')}})}
async function releaseHold(caseId,button){const reason=prompt('Причина снятия legal hold (не менее 10 символов):');if(!validReason(reason,'Причина снятия hold'))return;if(!confirm('Снять legal hold? После этого дело снова сможет пройти двухэтапное одобрение и удаление.'))return;return withCaseAction(caseId,button,async()=>{try{await api('/retention/cases/'+caseId+'/hold/release',{method:'POST',body:JSON.stringify({reason})});await refreshAfter(`Legal hold по делу #${caseId} снят`)}catch(e){feedback(`Legal hold по делу #${caseId} не снят: ${e.message}`,'bad')}})}
async function requestDelete(caseId,button){const reason=prompt('Обоснование удаления (не менее 10 символов):');if(!validReason(reason,'Обоснование удаления'))return;if(!confirm('Создать запрос на удаление содержимого дела? Одобрение должен выполнить другой суперадминистратор.'))return;return withCaseAction(caseId,button,async()=>{try{const result=await api('/retention/cases/'+caseId+'/request',{method:'POST',body:JSON.stringify({reason})});await refreshAfter(`Запрос #${result.record_id} по делу #${caseId} создан: ${result.status}`)}catch(e){feedback(`Запрос удаления по делу #${caseId} не создан: ${e.message}`,'bad')}})}
async function approve(recordId,caseId,button){const comment=prompt('Комментарий одобрения (не менее 10 символов). Запрос и одобрение должны выполнить разные суперадминистраторы:');if(!validReason(comment,'Комментарий одобрения'))return;if(!confirm('Одобрить необратимое удаление содержимого? После одобрения останется отдельный этап запуска удаления.'))return;return withCaseAction(caseId,button,async()=>{try{const result=await api('/retention/records/'+recordId+'/approve',{method:'POST',body:JSON.stringify({comment})});await refreshAfter(`Удаление по делу #${caseId} одобрено: ${result.status}`)}catch(e){feedback(`Удаление по делу #${caseId} не одобрено: ${e.message}`,'bad')}})}
async function executeDelete(recordId,caseId,button){const expected=button.dataset.caseNumber||'';const typed=prompt(`Для необратимого удаления введите номер дела: ${expected}`);if(typed!==expected){feedback('Удаление отменено: номер дела введён неверно','bad');return}if(!confirm('Последнее подтверждение: удалить документы, сообщения, уведомления и операционное содержимое дела? Платёжный ledger, номер дела и аудит сохранятся.'))return;return withCaseAction(caseId,button,async()=>{try{const result=await api('/retention/records/'+recordId+'/execute',{method:'POST',body:'{}'});await refreshAfter(`Содержимое дела #${caseId} удалено. Документы: ${result.documents_deleted}, сообщения: ${result.messages_deleted}, уведомления: ${result.notifications_deleted}, консультации обезличены: ${result.consultations_anonymized}. Digest: ${result.content_digest}`)}catch(e){feedback(`Содержимое дела #${caseId} не удалено: ${e.message}`,'bad')}})}
boot();
</script>
</body>
</html>
"""
