from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.lawyer_consultation_desk import CONSULTATION_DESK_HTML
from app.api.lawyer_workspace import WORKSPACE_HTML
from app.db.session import get_db
from app.domain.cases.sla_service import CaseSLAError, CaseSLAService
from app.domain.consultations.outcome_service import (
    ConsultationOutcomeError,
    ConsultationOutcomeService,
)
from app.models.case import Case
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
  const params=new URLSearchParams(window.location.search);
  const requested=Number(params.get('case_id')||0);
  if(!Number.isInteger(requested)||requested<=0)return;
  let applied=false;
  const originalRender=render;
  render=function(){
    originalRender();
    if(applied)return;
    const snapshot=(typeof data!=='undefined'?(data?.cases||[]):[]).find(item=>Number(item.case_id)===requested);
    if(!snapshot){
      if(typeof data!=='undefined'&&data){
        applied=true;
        feedback('Запрошенное дело не входит в ваши активные дела или уже закрыто. Показана актуальная рабочая очередь.','warn-text');
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


def _inject_patch(html: str, patch: str) -> str:
    marker = "</body>"
    if html.count(marker) != 1:
        raise RuntimeError("Lawyer UI template contract changed: </body> marker is not unique")
    return html.replace(marker, patch + marker, 1)


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
