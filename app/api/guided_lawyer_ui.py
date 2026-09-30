from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.lawyer_consultation_desk import CONSULTATION_DESK_HTML
from app.api.lawyer_workspace import (
    CLIENT_WAIT_STATUSES,
    CLOSED_CASE_STATUSES,
    WORKSPACE_HTML,
    workspace_data as legacy_workspace_data,
)
from app.db.session import get_db
from app.domain.cases.case_responsibility import effective_lawyer_ids_for_cases
from app.domain.cases.case_timeline import get_client_visible_status
from app.domain.cases.sla_service import CaseSLAError, CaseSLAService
from app.domain.consultations.outcome_service import (
    ConsultationOutcomeError,
    ConsultationOutcomeService,
)
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.document_statuses import DocumentStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.message import Message
from app.models.user import User
from app.security.lawyer_access import require_lawyer_actor

router = APIRouter(tags=["guided-lawyer-ui"])


_CONSULTATION_DRAFT_PATCH = r"""
<script>
(function(){
  const prefix='dlc:consultation-draft:v1:';
  function owner(){return (typeof data!=='undefined'&&data?.lawyer?.id)?String(data.lawyer.id):'unknown'}
  function key(id,type){return prefix+owner()+':'+String(id)+':'+String(type||'complete')}
  function read(id,type){try{const raw=sessionStorage.getItem(key(id,type));return raw?JSON.parse(raw):null}catch(_){return null}}
  function save(id,type){
    const form=document.getElementById('form_'+id);
    if(!form)return;
    const actualType=type||form.dataset.type||'complete';
    const result=document.getElementById('result_'+id)?.value||'';
    const decision=document.getElementById('decision_'+id)?.value||'';
    try{sessionStorage.setItem(key(id,actualType),JSON.stringify({result:result.slice(0,12000),decision}))}catch(_){}
  }
  function clear(id,type){try{sessionStorage.removeItem(key(id,type))}catch(_){}}
  function bind(id,type){
    const result=document.getElementById('result_'+id),decision=document.getElementById('decision_'+id);
    if(result&&!result.dataset.draftBound){result.dataset.draftBound='1';result.addEventListener('input',()=>save(id,type))}
    if(decision&&!decision.dataset.draftBound){decision.dataset.draftBound='1';decision.addEventListener('change',()=>save(id,type))}
  }
  const originalOpen=openForm;
  openForm=function(id,type){
    originalOpen(id,type);
    const draft=read(id,type);
    if(draft){
      const result=document.getElementById('result_'+id),decision=document.getElementById('decision_'+id);
      if(result)result.value=String(draft.result||'');
      if(decision&&type==='complete')decision.value=String(draft.decision||'');
    }
    bind(id,type);
  };
  const originalClose=closeForm;
  closeForm=function(id){
    const form=document.getElementById('form_'+id);
    if(form?.dataset.type)save(id,form.dataset.type);
    originalClose(id);
  };
  const originalSubmit=submitResult;
  submitResult=async function(id,button){
    const form=document.getElementById('form_'+id),type=form?.dataset.type||'complete';
    save(id,type);
    const result=await originalSubmit(id,button);
    const current=(typeof data!=='undefined'?(data?.consultations||[]):[]).find(item=>Number(item.consultation_id)===Number(id));
    if((type==='complete'&&!current)||(type==='no_show'&&current?.state==='client_no_show'))clear(id,type);
    return result;
  };
})();
</script>
"""


_WORKSPACE_DEEP_LINK_PATCH = r"""
<script>
(function(){
  const legacyPrimaryButton=primaryButton;
  primaryButton=function(x){
    if(x?.route!=='M2')return legacyPrimaryButton(x);
    const id=Number(x.case_id);
    if(x.unread_client_messages)return `<a class="button" href="/message-center/ui?case_id=${id}">Ответить клиенту</a>`;
    if(x.consultation_status==='BOOKED')return `<a class="button green" href="/lawyer/consultation-desk/ui">Открыть консультацию</a>`;
    if(Number(x.documents_total||0)>0)return `<a class="button amber" href="/document-access/ui?case_id=${id}">Открыть материалы</a>`;
    return `<a class="button secondary" href="/message-center/ui?case_id=${id}">Открыть контекст</a>`;
  };
  const legacyCaseCard=caseCard;
  caseCard=function(x){
    let html=legacyCaseCard(x);
    if(x?.route!=='M2')return html;
    const id=Number(x.case_id);
    html=html.split('/document-access/review/ui?case_id='+id).join('/document-access/ui?case_id='+id);
    const when=x.consultation_scheduled_at?dt(x.consultation_scheduled_at):'время не назначено';
    const state=x.consultation_status_label||x.consultation_status||'консультация';
    const docs=Number(x.documents_total||0)>0?`<a class="button secondary" href="/document-access/ui?case_id=${id}">Материалы (${Number(x.documents_total||0)})</a>`:'';
    const guided=`<div class="deadline"><b>Контур консультации: ${esc(state)}</b><div class="muted">${esc(when)} · Ответственный определяется выбранным слотом, без фиктивного назначения М1.</div><div class="actions" style="margin-top:9px"><a class="button green" href="/lawyer/consultation-desk/ui">Консультации</a><a class="button secondary" href="/message-center/ui?case_id=${id}">Переписка</a>${docs}</div></div>`;
    return html.replace('</article>',guided+'</article>');
  };

  const params=new URLSearchParams(window.location.search);
  const requested=Number(params.get('case_id')||0);
  let applied=false;
  const originalRender=render;
  render=function(){
    originalRender();
    if(!Number.isInteger(requested)||requested<=0||applied)return;
    const snapshot=(typeof data!=='undefined'?(data?.cases||[]):[]).find(item=>Number(item.case_id)===requested);
    if(!snapshot){
      if(typeof data!=='undefined'&&data){
        applied=true;
        feedback('Запрошенное дело не входит в вашу текущую ответственность или уже закрыто. Показана актуальная рабочая очередь.','warn-text');
      }
      return;
    }
    applied=true;
    currentTab='cases';
    search.value='';
    const buttons=document.querySelectorAll('.tabs button');
    buttons.forEach((button,index)=>button.classList.toggle('active',index===2));
    originalRender();
    const card=document.getElementById('case_'+requested);
    if(card){
      card.style.boxShadow='0 0 0 3px #c7d2fe, 0 12px 34px rgba(16,24,40,.07)';
      card.scrollIntoView({behavior:'smooth',block:'center'});
      feedback('Открыто дело '+String(snapshot.case_number||requested)+'.','ok');
    }
  };
})();
</script>
"""


_CONSULTATION_STATUS_LABELS = {
    ConsultationStatus.SLOT_RESERVED.value: "Время зарезервировано",
    ConsultationStatus.PAYMENT_PENDING.value: "Ожидается оплата",
    ConsultationStatus.BOOKED.value: "Консультация подтверждена",
    ConsultationStatus.DONE.value: "Результат консультации зафиксирован",
    ConsultationStatus.CLIENT_NO_SHOW.value: "Клиент не явился",
    ConsultationStatus.LAWYER_NO_SHOW.value: "Неявка юриста — требуется эскалация",
}


def _inject_patch(html: str, patch: str) -> str:
    marker = "</body>"
    if html.count(marker) != 1:
        raise RuntimeError("Lawyer UI template contract changed: </body> marker is not unique")
    return html.replace(marker, patch + marker, 1)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _slot_owned_m2_cards(
    db: AsyncSession,
    *,
    lawyer_id: int,
    now: datetime,
) -> list[dict[str, object]]:
    rows = (
        await db.execute(
            select(Case, User)
            .join(User, User.id == Case.client_id)
            .where(Case.route == "M2")
            .where(Case.status.notin_(CLOSED_CASE_STATUSES))
            .order_by(Case.updated_at.desc(), Case.id.desc())
            .limit(600)
        )
    ).all()
    if not rows:
        return []

    cases = [case for case, _ in rows]
    responsibilities = await effective_lawyer_ids_for_cases(db, cases)
    owned_rows = [
        (case, user)
        for case, user in rows
        if responsibilities.get(int(case.id)) == int(lawyer_id)
    ]
    if not owned_rows:
        return []

    case_ids = [int(case.id) for case, _ in owned_rows]
    documents_total: dict[int, int] = defaultdict(int)
    documents_on_review: dict[int, int] = defaultdict(int)
    unread_by_case: dict[int, int] = defaultdict(int)
    latest_consultation: dict[int, Consultation] = {}

    for item in (
        await db.execute(
            select(Document)
            .where(Document.case_id.in_(case_ids))
            .where(Document.status != DocumentStatus.ARCHIVED)
        )
    ).scalars().all():
        documents_total[int(item.case_id)] += 1
        if str(item.status) == DocumentStatus.ON_REVIEW.value:
            documents_on_review[int(item.case_id)] += 1

    for item in (
        await db.execute(
            select(Message)
            .where(Message.case_id.in_(case_ids))
            .where(Message.sender_type == "client")
            .where(Message.is_read.is_(False))
        )
    ).scalars().all():
        unread_by_case[int(item.case_id)] += 1

    for consultation in (
        await db.execute(
            select(Consultation)
            .where(Consultation.case_id.in_(case_ids))
            .order_by(
                Consultation.case_id.asc(),
                Consultation.created_at.desc(),
                Consultation.id.desc(),
            )
        )
    ).scalars().all():
        latest_consultation.setdefault(int(consultation.case_id), consultation)

    cards: list[dict[str, object]] = []
    for case, user in owned_rows:
        consultation = latest_consultation.get(int(case.id))
        consultation_status = str(consultation.status) if consultation else None
        scheduled_at = consultation.scheduled_at if consultation else None
        consultation_today = bool(
            consultation_status == ConsultationStatus.BOOKED.value
            and scheduled_at is not None
            and _utc(scheduled_at).date() == now.date()
        )
        unread = int(unread_by_case.get(int(case.id), 0))
        docs_total = int(documents_total.get(int(case.id), 0))
        docs_review = int(documents_on_review.get(int(case.id), 0))

        priority = "normal"
        recommended_action = case.next_action or "Проверить контекст консультации"
        action_note: str | None = None
        if unread:
            priority = "high"
            recommended_action = "Ответить клиенту"
            action_note = f"Непрочитанных сообщений: {unread}"
        elif consultation_status == ConsultationStatus.BOOKED.value:
            recommended_action = (
                "Открыть консультацию" if consultation_today else "Подготовиться к консультации"
            )
            action_note = (
                f"Назначено на {scheduled_at.isoformat()}" if scheduled_at else None
            )
            if consultation_today:
                priority = "high"
        elif consultation_status in {
            ConsultationStatus.SLOT_RESERVED.value,
            ConsultationStatus.PAYMENT_PENDING.value,
        }:
            recommended_action = "Ожидать подтверждения оплаты"
            action_note = "Слот уже определил ответственного юриста; повторное назначение дела не требуется."
        elif consultation_status == ConsultationStatus.CLIENT_NO_SHOW.value:
            priority = "high"
            recommended_action = "Определить перенос или закрытие обращения"
            action_note = "Неявка клиента уже зафиксирована; требуется завершённый следующий шаг."
        elif consultation_status == ConsultationStatus.LAWYER_NO_SHOW.value:
            priority = "critical"
            recommended_action = "Передать администратору на бесплатный перенос"
            action_note = "Неявка юриста требует административной компенсации и нового слота."
        elif consultation_status == ConsultationStatus.DONE.value:
            priority = "high"
            recommended_action = case.next_action or "Проверить решение после консультации"
            action_note = "Результат зафиксирован; не оставляйте дело без следующего действия."

        cards.append(
            {
                "case_id": case.id,
                "case_number": case.case_number,
                "client_name": user.full_name,
                "route": "M2",
                "route_label": "Консультация",
                "status": case.status,
                "status_label": get_client_visible_status(case.status),
                "updated_at": case.updated_at.isoformat(),
                "sla_status": "NOT_STARTED",
                "sla_label": "Контроль по времени консультации",
                "sla_due_at": scheduled_at.isoformat() if scheduled_at else None,
                "unread_client_messages": unread,
                "documents_on_review": docs_review,
                "documents_total": docs_total,
                "documents_ready": False,
                "readiness_reason": None,
                "consultation_today": consultation_today,
                "consultation_today_at": scheduled_at.isoformat() if consultation_today else None,
                "consultation_id": int(consultation.id) if consultation else None,
                "consultation_status": consultation_status,
                "consultation_status_label": _CONSULTATION_STATUS_LABELS.get(
                    consultation_status or "",
                    consultation_status or "Консультация",
                ),
                "consultation_scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
                "priority": priority,
                "recommended_action": recommended_action,
                "action_note": action_note,
                "can_accept": False,
                "can_request_documents": False,
                "can_transfer_to_m2": False,
                "m1_action": None,
                "claim_due_at": None,
                "claim_remaining_seconds": 0,
                "claim_note": None,
                "success_fee_payment": None,
            }
        )
    return cards


def _rebuild_workspace_summary(cases: list[dict[str, object]]) -> dict[str, int]:
    return {
        "active_cases": len(cases),
        "requires_action": sum(
            str(item.get("priority") or "") in {"critical", "high"} for item in cases
        ),
        "unread_client_messages": sum(
            int(item.get("unread_client_messages") or 0) for item in cases
        ),
        "documents_on_review": sum(
            int(item.get("documents_on_review") or 0) for item in cases
        ),
        "overdue": sum(
            str(item.get("sla_status") or "").endswith("OVERDUE") for item in cases
        ),
        "waiting": sum(
            str(item.get("m1_action") or "") in {"wait_claim_period", "wait_success_fee"}
            or str(item.get("status") or "") in {status.value for status in CLIENT_WAIT_STATUSES}
            for item in cases
        ),
        "consultations_today": sum(bool(item.get("consultation_today")) for item in cases),
    }


@router.get("/lawyer/workspace/data")
async def guided_workspace_data(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Return M1 assignments plus M2 cases owned by the consultation slot.

    The legacy workspace uses Case.assigned_lawyer_id for every route. That is
    correct for M1 but not for M2, where Consultation.lawyer_id is the source of
    truth. Rebuilding M2 here both adds the legitimate consultation cases and
    removes stale/incorrect generic M2 assignments from a lawyer's queue.
    """

    actor = await require_lawyer_actor(db, x_admin_token)
    payload = await legacy_workspace_data(db=db, x_admin_token=x_admin_token)
    now = datetime.now(timezone.utc)

    m1_cards = [item for item in list(payload.get("cases") or []) if item.get("route") != "M2"]
    m2_cards = await _slot_owned_m2_cards(db, lawyer_id=actor.lawyer.id, now=now)
    cases = m1_cards + m2_cards
    cases.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    payload["generated_at"] = now.isoformat()
    payload["cases"] = cases
    payload["summary"] = _rebuild_workspace_summary(cases)
    return payload


async def _case_after_consultation_outcome(
    db: AsyncSession,
    *,
    case_id: int,
    lawyer_id: int,
    action: str,
    comment: str | None,
) -> Case:
    """Return the M2 case and update assignment SLA only when one exists.

    M2 ownership is defined by Consultation.lawyer_id from the booked calendar
    slot. A Case.assigned_lawyer_id is intentionally optional for that route, so
    requiring a generic M1 case assignment after a valid consultation outcome
    makes the normal M2 completion/no-show path fail. If an M2 case was
    explicitly assigned as an operational exception, its SLA is still updated.
    """

    case = await db.get(Case, case_id)
    if case is None:
        raise LookupError("Дело не найдено")
    if case.assigned_lawyer_id == lawyer_id:
        await CaseSLAService(db).record_lawyer_activity(
            case=case,
            lawyer_id=lawyer_id,
            action=action,
            comment=comment,
        )
    return case


@router.post("/lawyer/consultations/{consultation_id}/complete")
async def guided_complete_consultation(
    consultation_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Complete the consultation using slot ownership, not generic case assignment."""

    actor = await require_lawyer_actor(db, x_admin_token)
    try:
        consultation = await ConsultationOutcomeService(db).complete(
            consultation_id=consultation_id,
            lawyer_id=actor.lawyer.id,
            result=payload.get("result") or "",
            decision=payload.get("decision") or "",
            expected_slot_id=payload.get("expected_slot_id"),
        )
        case = await _case_after_consultation_outcome(
            db,
            case_id=consultation.case_id,
            lawyer_id=actor.lawyer.id,
            action="CONSULTATION_COMPLETED",
            comment=payload.get("result"),
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ConsultationOutcomeError, CaseSLAError, ValueError) as error:
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


@router.post("/lawyer/consultations/{consultation_id}/client-no-show")
async def guided_client_no_show(
    consultation_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Record client no-show without inventing a Case assignment for M2."""

    actor = await require_lawyer_actor(db, x_admin_token)
    try:
        consultation = await ConsultationOutcomeService(db).mark_client_no_show(
            consultation_id=consultation_id,
            lawyer_id=actor.lawyer.id,
            comment=payload.get("comment") or "",
            expected_slot_id=payload.get("expected_slot_id"),
        )
        case = await _case_after_consultation_outcome(
            db,
            case_id=consultation.case_id,
            lawyer_id=actor.lawyer.id,
            action="CONSULTATION_CLIENT_NO_SHOW",
            comment=payload.get("comment"),
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ConsultationOutcomeError, CaseSLAError, ValueError) as error:
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


@router.get("/lawyer/consultation-desk/ui", response_class=HTMLResponse)
async def guided_consultation_desk_ui():
    """Preserve an unsaved consultation outcome when the lawyer closes the form."""

    return HTMLResponse(_inject_patch(CONSULTATION_DESK_HTML, _CONSULTATION_DRAFT_PATCH))


@router.get("/lawyer/workspace/ui", response_class=HTMLResponse)
async def guided_lawyer_workspace_ui():
    """Honor case_id deep links used by consultation, documents and message flows."""

    return HTMLResponse(_inject_patch(WORKSPACE_HTML, _WORKSPACE_DEEP_LINK_PATCH))