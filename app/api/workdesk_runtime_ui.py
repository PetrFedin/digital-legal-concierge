from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.workdesk_integrity import _WORKDESK_INTEGRITY_PATCH
from app.api.workdesk_ui import WORKDESK_HTML
from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor


_WORKDESK_PRODUCT_EXTENSION = r"""
<script>
(function(){
  const headerLinks=document.querySelector('header .links');
  const legacyAdminLink=headerLinks?.querySelector('a[href="/admin-ui"]');
  if(legacyAdminLink){
    legacyAdminLink.href='/consultation-slots/ui';
    legacyAdminLink.textContent='Расписание';
    legacyAdminLink.title='Свободные слоты, резервы и расписание юристов';
  }

  // Active cases are a distinct operational projection. The base shell used to
  // open the unassigned queue from this metric; keep the displayed number and
  // the opened list semantically identical.
  titles.active='Все активные дела';
  const originalQAction=qaction;
  qaction=function(item){
    if(queue==='active')return'';
    return originalQAction(item);
  };
  const originalLoadOverview=loadOverview;
  loadOverview=async function(){
    const result=await originalLoadOverview();
    const activeMetric=document.querySelector('#view .metrics .metric');
    if(activeMetric){
      activeMetric.onclick=()=>openQueue('active',null);
      activeMetric.title='Открыть все активные обращения';
    }
    return result;
  };

  const originalOpenCase=openCase;
  const originalCloseCase=closeCase;
  const originalAssign=assign;

  function caseCell(label){
    return [...cv.querySelectorAll('.grid .cell')].find(
      node=>node.querySelector('span')?.textContent.trim()===label
    );
  }
  function replaceCell(node,label,value){
    if(node)node.innerHTML='<span>'+e(label)+'</span>'+e(value||'—');
  }
  function syncCaseUrl(id){
    const url=new URL(window.location.href);
    if(Number.isInteger(id)&&id>0)url.searchParams.set('case_id',String(id));
    else url.searchParams.delete('case_id');
    history.replaceState(null,'',url.pathname+url.search+url.hash);
  }
  function applyGuidedCaseHierarchy(){
    const actionBox=cv.querySelector('.action');
    if(!actionBox)return;
    const heading=actionBox.querySelector('b');
    if(heading)heading.textContent='Сейчас';
    if(actionBox.querySelector('.main-step-label'))return;
    const actionText=actionBox.querySelector('div');
    if(!actionText)return;
    const label=document.createElement('span');
    label.className='eyebrow main-step-label';
    label.textContent='Главный следующий шаг';
    actionBox.insertBefore(label,actionText);
  }

  // Reassignment of a case whose stored lawyer can no longer operate must use
  // the snapshot-locked repair action, not a generic auto-assign retry.
  assign=async function(id,b){
    let snapshot;
    try{
      snapshot=await api('/admin/case-workspace/'+id);
    }catch(x){
      say('Не удалось проверить текущее назначение: '+(x.message||x),'bad');
      return;
    }
    const currentLawyer=Number(snapshot.case?.lawyer_id||0);
    if(!currentLawyer)return originalAssign(id,b);
    if(busy)return;
    busy=true;
    if(b)b.disabled=true;
    const old=b?b.textContent:'';
    if(b)b.textContent='Проверяем назначение…';
    try{
      const result=await api('/admin/case-assignment/cases/'+id+'/repair-unreachable',{
        method:'POST',
        body:JSON.stringify({
          expected_lawyer_id:currentLawyer,
          expected_status:snapshot.case.status,
          comment:'Workdesk: назначенный юрист недоступен для персонального входа'
        })
      });
      say(
        result.result==='reassigned'
          ?'Недоступный ответственный заменён доступным юристом.'
          :'Недоступное назначение снято. Дело осталось в контролируемой очереди и не потеряно.',
        'ok'
      );
      await openCase(id);
      if(mode==='queue')await openQueue(queue,document.querySelector('[data-v='+queue+']'));
      else await loadOverview();
      if(typeof loadProcessIntegrity==='function')await loadProcessIntegrity();
    }catch(x){
      say('Исправление назначения не выполнено: '+(x.message||x),'bad');
      await openCase(id);
    }finally{
      busy=false;
      if(b){b.disabled=false;b.textContent=old;}
    }
  };

  openCase=async function(rawId){
    const id=Number(rawId);
    const result=await originalOpenCase(id);
    if(!Number.isInteger(id)||id<=0||selected!==id)return result;
    if(!cv.querySelector('.error')){
      syncCaseUrl(id);
      applyGuidedCaseHierarchy();
    }

    let responsibility;
    try{
      responsibility=await api('/admin/workdesk/cases/'+id+'/responsibility');
    }catch(_){
      return result;
    }
    if(selected!==id||responsibility.route!=='M2')return result;

    replaceCell(
      caseCell('Ответственный'),
      'Юрист консультации',
      responsibility.lawyer_name||'будет определён выбранным слотом'
    );
    replaceCell(
      caseCell('SLA'),
      'Контроль консультации',
      responsibility.lawyer_name?'по выбранному слоту':'ожидается выбор слота'
    );
    replaceCell(
      caseCell('Срок'),
      'Время консультации',
      responsibility.scheduled_at?dt(responsibility.scheduled_at):'ещё не выбрано'
    );
    const primary=cv.querySelector('.action div');
    if(primary&&responsibility.next_action)primary.textContent=responsibility.next_action;
    [...cv.querySelectorAll('.section a,.section button')].forEach(node=>{
      const label=node.textContent.trim();
      if(label==='SLA'||label==='Назначить перед SLA')node.remove();
    });
    return result;
  };

  closeCase=function(){
    const result=originalCloseCase();
    syncCaseUrl(null);
    return result;
  };

  // Deep links are read-only navigation. Wait until the HttpOnly session has
  // been represented by the browser sentinel, then open exactly that Case.
  const requestedCaseId=Number(
    new URLSearchParams(window.location.search).get('case_id')||0
  );
  if(Number.isInteger(requestedCaseId)&&requestedCaseId>0){
    let attempts=0;
    const timer=setInterval(()=>{
      attempts+=1;
      if(token){
        clearInterval(timer);
        void openCase(requestedCaseId).then(()=>{
          if(!cv.querySelector('.error')){
            say('Открыто дело из предыдущего рабочего экрана.','ok');
            cv.scrollIntoView({behavior:'smooth',block:'start'});
          }
        }).catch(()=>{});
      }else if(attempts>=40){
        clearInterval(timer);
      }
    },100);
  }
})();
</script>
"""


def _append_body_extensions(html: str, *extensions: str) -> str:
    """Compose Workdesk JS through one structural body boundary.

    This deliberately avoids replacing internal JavaScript literals. The base
    Workdesk can refactor its function bodies without silently disabling M2,
    active-queue or deep-link safety behavior.
    """

    head, marker, tail = html.rpartition("</body>")
    if not marker:
        raise RuntimeError("Workdesk template contract changed: closing body missing")
    return head + "\n" + "\n".join(extensions) + "\n" + marker + tail


def render_workdesk_runtime_html() -> str:
    before_main, marker, after_main = WORKDESK_HTML.partition("<main>")
    if not marker:
        raise RuntimeError("Workdesk template contract changed: main region missing")
    html = (
        before_main
        + marker
        + '<div id="processIntegrityBanner"></div>'
        + after_main
    )
    return _append_body_extensions(
        html,
        _WORKDESK_PRODUCT_EXTENSION,
        _WORKDESK_INTEGRITY_PATCH,
    )


async def workdesk_runtime_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Serve the canonical Workdesk through a live personal admin session."""

    token = x_admin_token or request.cookies.get(settings.admin_session_cookie)
    if not token:
        return RedirectResponse(url="/login", status_code=303)
    try:
        actor = await resolve_document_actor(db, token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        if error.status_code in {403, 409}:
            return RedirectResponse(url="/admin-ui", status_code=303)
        raise
    except HTTPException as error:
        if error.status_code in {403, 409}:
            return RedirectResponse(url="/admin-ui", status_code=303)
        raise
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        return RedirectResponse(url="/admin-ui", status_code=303)
    return HTMLResponse(render_workdesk_runtime_html())


__all__ = ["render_workdesk_runtime_html", "workdesk_runtime_ui"]
