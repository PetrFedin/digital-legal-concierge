from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.domain.cases.sla_service import CaseSLAError, CaseSLAService
from app.domain.consultations.outcome_service import (
    ConsultationOutcomeError,
    ConsultationOutcomeService,
)
from app.lawyer.lawyer_decisions import LawyerDecisionService
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.user import User
from app.security.lawyer_access import require_lawyer_actor

router = APIRouter(prefix="/lawyer", tags=["lawyer"])


async def assigned_case(
    db: AsyncSession,
    case_id: int,
    lawyer_id: int,
) -> Case:
    case = await db.get(Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Дело не найдено")
    if case.assigned_lawyer_id != lawyer_id:
        raise HTTPException(
            status_code=403,
            detail="Дело не назначено текущему юристу",
        )
    return case


@router.get("/consultations")
async def lawyer_consultations(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await require_lawyer_actor(db, x_admin_token)
    rows = (
        await db.execute(
            select(Consultation, Case, User, ConsultationSlot)
            .join(Case, Case.id == Consultation.case_id)
            .join(User, User.id == Case.client_id)
            .outerjoin(
                ConsultationSlot,
                ConsultationSlot.id == Consultation.slot_id,
            )
            .where(Consultation.lawyer_id == actor.lawyer.id)
            .order_by(
                Consultation.scheduled_at.asc(),
                Consultation.created_at.desc(),
            )
            .limit(200)
        )
    ).all()
    await db.commit()
    return [
        {
            "consultation_id": consultation.id,
            "case_id": case.id,
            "case_number": case.case_number,
            "client_id": user.id,
            "client_name": user.full_name,
            "telegram_id": user.telegram_id,
            "status": consultation.status,
            "scheduled_at": (
                consultation.scheduled_at.isoformat()
                if consultation.scheduled_at
                else None
            ),
            "slot_starts_at": (
                slot.starts_at.isoformat() if slot else None
            ),
            "slot_ends_at": slot.ends_at.isoformat() if slot else None,
            "slot_status": slot.status if slot else None,
            "subject": consultation.client_description,
            "result": consultation.lawyer_result,
            "decision": consultation.decision,
            "case_sla_status": case.sla_status,
            "case_sla_due_at": (
                case.sla_due_at.isoformat() if case.sla_due_at else None
            ),
            "case_escalation_level": int(case.escalation_level or 0),
        }
        for consultation, case, user, slot in rows
    ]


@router.post("/cases/{case_id}/accept")
async def accept(
    case_id: int,
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await require_lawyer_actor(db, x_admin_token)
    case = await assigned_case(db, case_id, actor.lawyer.id)
    try:
        await LawyerDecisionService(db).accept_m1_case(
            case=case,
            lawyer_id=actor.lawyer.id,
            comment=(payload or {}).get("comment"),
        )
        await CaseSLAService(db).record_lawyer_activity(
            case=case,
            lawyer_id=actor.lawyer.id,
            action="M1_CASE_ACCEPTED",
            comment=(payload or {}).get("comment"),
        )
        await db.commit()
    except (ValueError, CaseSLAError) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise
    return {
        "ok": True,
        "case_id": case.id,
        "status": case.status,
        "sla_status": case.sla_status,
        "sla_due_at": case.sla_due_at,
    }


@router.post("/cases/{case_id}/request-documents")
async def request_docs(
    case_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await require_lawyer_actor(db, x_admin_token)
    case = await assigned_case(db, case_id, actor.lawyer.id)
    comment = str(payload.get("comment") or "").strip()
    if len(comment) < 5:
        raise HTTPException(
            status_code=400,
            detail="Укажите, какие документы необходимо предоставить",
        )
    try:
        await LawyerDecisionService(db).request_more_documents(
            case=case,
            lawyer_id=actor.lawyer.id,
            comment=comment,
        )
        await CaseSLAService(db).record_lawyer_activity(
            case=case,
            lawyer_id=actor.lawyer.id,
            action="DOCUMENTS_REQUESTED",
            comment=comment,
        )
        await db.commit()
    except (ValueError, CaseSLAError) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise
    return {
        "ok": True,
        "case_id": case.id,
        "status": case.status,
        "sla_status": case.sla_status,
        "sla_due_at": case.sla_due_at,
    }


@router.post("/consultations/{consultation_id}/complete")
async def complete_consultation(
    consultation_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await require_lawyer_actor(db, x_admin_token)
    try:
        consultation = await ConsultationOutcomeService(db).complete(
            consultation_id=consultation_id,
            lawyer_id=actor.lawyer.id,
            result=payload.get("result") or "",
            decision=payload.get("decision") or "",
        )
        case = await assigned_case(
            db,
            consultation.case_id,
            actor.lawyer.id,
        )
        await CaseSLAService(db).record_lawyer_activity(
            case=case,
            lawyer_id=actor.lawyer.id,
            action="CONSULTATION_COMPLETED",
            comment=payload.get("result"),
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (
        ConsultationOutcomeError,
        CaseSLAError,
        ValueError,
    ) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise
    return {
        "ok": True,
        "consultation_id": consultation.id,
        "status": consultation.status,
        "decision": consultation.decision,
        "sla_status": case.sla_status,
        "sla_due_at": case.sla_due_at,
    }


@router.post("/consultations/{consultation_id}/client-no-show")
async def client_no_show(
    consultation_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await require_lawyer_actor(db, x_admin_token)
    try:
        consultation = await ConsultationOutcomeService(db).mark_client_no_show(
            consultation_id=consultation_id,
            lawyer_id=actor.lawyer.id,
            comment=payload.get("comment") or "",
        )
        case = await assigned_case(
            db,
            consultation.case_id,
            actor.lawyer.id,
        )
        await CaseSLAService(db).record_lawyer_activity(
            case=case,
            lawyer_id=actor.lawyer.id,
            action="CONSULTATION_CLIENT_NO_SHOW",
            comment=payload.get("comment"),
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (
        ConsultationOutcomeError,
        CaseSLAError,
        ValueError,
    ) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise
    return {
        "ok": True,
        "consultation_id": consultation.id,
        "status": consultation.status,
        "sla_status": case.sla_status,
        "sla_due_at": case.sla_due_at,
    }


@router.get("/ui", response_class=HTMLResponse)
async def lawyer_ui():
    return HTMLResponse(LAWYER_HTML)


LAWYER_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Кабинет юриста</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f4f5f7;margin:0;color:#111827}header{background:#111827;color:#fff;padding:18px 24px;display:flex;justify-content:space-between}main{max-width:1300px;margin:auto;padding:24px}.card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:16px}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}button{border:0;border-radius:9px;padding:8px 11px;color:#fff;background:#2563eb;font-weight:700;cursor:pointer}button:disabled,select:disabled{opacity:.55;cursor:wait}.red{background:#b91c1c}.muted{font-size:13px;color:#6b7280}.ok{color:#15803d}.bad{color:#b91c1c}.warn{color:#a16207}.actions{display:grid;gap:6px}.actions select{width:100%;padding:8px;border:1px solid #d1d5db;border-radius:8px}.badge{display:inline-block;border-radius:999px;padding:4px 8px;background:#e5e7eb;font-size:12px}.overdue{background:#fee2e2;color:#991b1b}@media(max-width:900px){table{display:block;overflow-x:auto;font-size:12px}}
</style>
</head>
<body>
<header><b>⚖ Кабинет юриста</b><a href="/admin-ui" style="color:white">Админка</a></header>
<main><div class="card"><h2>Мои консультации</h2><div id="content">Загрузка…</div></div><div id="message" class="muted" role="status" aria-live="polite"></div></main>
<script>
let token='';const pendingConsultations=new Set();
async function api(path,opts={}){const r=await fetch(path,{...opts,headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка');return d}
function feedback(text,state='ok'){message.textContent=text;message.className='muted '+state}
function consultationControls(id){return Array.from(document.querySelectorAll(`[data-consultation-id="${id}"]`))}
async function withConsultationAction(id,button,work){if(pendingConsultations.has(id))return;pendingConsultations.add(id);const controls=consultationControls(id);const labels=new Map(controls.filter(x=>x.tagName==='BUTTON').map(x=>[x,x.textContent]));controls.forEach(x=>{x.disabled=true;x.setAttribute('aria-busy','true')});if(button)button.textContent='Выполняется…';try{return await work()}finally{pendingConsultations.delete(id);controls.forEach(x=>{x.disabled=false;x.removeAttribute('aria-busy')});labels.forEach((label,x)=>{x.textContent=label})}}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function dt(v){return v?new Date(v).toLocaleString('ru-RU'):'—'}
function slaClass(v){return String(v||'').includes('OVERDUE')?'badge overdue':'badge'}
function decisionOptions(id){return `<select data-consultation-id="${id}" id="decision_${id}"><option value="">Выберите итоговое решение</option><option value="close">Закрыть обращение</option><option value="to_m1">Перевести в маршрут М1</option><option value="follow_up">Нужна следующая консультация</option><option value="other">Иное решение</option></select>`}
function decisionLabel(value){return {close:'закрыть обращение',to_m1:'перевести дело в маршрут М1',follow_up:'назначить следующую консультацию',other:'зафиксировать иное решение'}[value]||value}
async function boot(){const r=await fetch('/auth/session');if(!r.ok){location.href='/login';return}const s=await r.json();if(!(s.roles||[s.role]).includes('lawyer')){content.innerHTML='Недостаточно прав: требуется роль юриста.';return}token=s.api_token;try{await load()}catch(e){feedback(e.message,'bad')}}
async function load(){const rows=await api('/lawyer/consultations');content.innerHTML=rows.length?`<table><tr><th>Встреча</th><th>Дело / клиент</th><th>Вопрос</th><th>Статус / SLA</th><th>Действия</th></tr>${rows.map(x=>`<tr><td>${esc(dt(x.scheduled_at))}<br><span class="muted">${esc(x.slot_status||'')}</span></td><td><b>${esc(x.case_number)}</b><br>${esc(x.client_name)}<br><span class="muted">TG ${esc(x.telegram_id)}</span></td><td>${esc(x.subject||'не указан')}</td><td><span class="badge">${esc(x.status)}</span><br><span class="${slaClass(x.case_sla_status)}">${esc(x.case_sla_status||'NOT_STARTED')}</span><br><span class="muted">до ${esc(dt(x.case_sla_due_at))}<br>эскалация ${esc(x.case_escalation_level)}</span></td><td><div class="actions">${x.status==='BOOKED'?`${decisionOptions(x.consultation_id)}<button data-consultation-id="${x.consultation_id}" onclick="completeConsultation(${x.consultation_id},this)">Зафиксировать результат</button><button data-consultation-id="${x.consultation_id}" class="red" onclick="noShow(${x.consultation_id},this)">Клиент не явился</button>`:'—'}</div></td></tr>`).join('')}</table>`:'Консультаций нет.'}
async function completeConsultation(id,button){const decision=document.getElementById('decision_'+id).value;if(!decision){feedback('Выберите итоговое решение по консультации','bad');return}const result=prompt('Опишите результат консультации (минимум 20 символов):');if(!result)return;if(result.trim().length<20){feedback('Результат должен содержать не менее 20 символов','bad');return}if(!confirm(`Подтвердите итог консультации #${id}: ${decisionLabel(decision)}.`))return;return withConsultationAction(id,button,async()=>{try{const response=await api('/lawyer/consultations/'+id+'/complete',{method:'POST',body:JSON.stringify({result,decision})});feedback(`Результат консультации #${response.consultation_id} сохранён: ${response.decision}`,'ok');try{await load()}catch(e){feedback(`Результат сохранён, но список не обновился: ${e.message}`,'warn')}}catch(e){feedback(`Результат консультации #${id} не сохранён: ${e.message}`,'bad')}})}
async function noShow(id,button){const comment=prompt('Укажите обстоятельства неявки клиента:');if(!comment)return;if(comment.trim().length<5){feedback('Комментарий должен содержать не менее 5 символов','bad');return}if(!confirm(`Подтвердите неявку клиента по консультации #${id}. Зафиксировать результат этой встречи после этого будет нельзя.`))return;return withConsultationAction(id,button,async()=>{try{const response=await api('/lawyer/consultations/'+id+'/client-no-show',{method:'POST',body:JSON.stringify({comment})});feedback(`Неявка клиента по консультации #${response.consultation_id} зафиксирована`,'ok');try{await load()}catch(e){feedback(`Неявка сохранена, но список не обновился: ${e.message}`,'warn')}}catch(e){feedback(`Неявка по консультации #${id} не сохранена: ${e.message}`,'bad')}})}
boot();
</script>
</body>
</html>
"""
