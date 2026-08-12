from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.api.lawyer_consultation_desk import CONSULTATION_DESK_HTML
from app.api.lawyer_workspace import WORKSPACE_HTML

router = APIRouter(tags=["guided-lawyer-ui"])


_CONSULTATION_DRAFT_PATCH = r"""
<script>
(function(){
  const prefix='dlc:consultation-draft:v1:';
  function key(id,type){return prefix+String(id)+':'+String(type||'complete')}
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


@router.get("/lawyer/consultation-desk/ui", response_class=HTMLResponse)
async def guided_consultation_desk_ui():
    """Preserve an unsaved consultation outcome when the lawyer closes the form."""

    return HTMLResponse(_inject_patch(CONSULTATION_DESK_HTML, _CONSULTATION_DRAFT_PATCH))


@router.get("/lawyer/workspace/ui", response_class=HTMLResponse)
async def guided_lawyer_workspace_ui():
    """Honor case_id deep links used by consultation, documents and message flows."""

    return HTMLResponse(_inject_patch(WORKSPACE_HTML, _WORKSPACE_DEEP_LINK_PATCH))
