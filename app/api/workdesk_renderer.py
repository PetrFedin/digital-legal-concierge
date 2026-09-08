from __future__ import annotations

from app.api.workdesk_integrity import inject_workdesk_integrity
from app.api.workdesk_ui import WORKDESK_HTML


_WORKDESK_RESPONSIBILITY_PATCH = r"""
<script>
(function(){
  const headerLinks=document.querySelector('header .links');
  const legacyAdminLink=headerLinks?.querySelector('a[href="/admin-ui"]');
  if(legacyAdminLink){
    legacyAdminLink.href='/consultation-slots/ui';
    legacyAdminLink.textContent='Расписание';
    legacyAdminLink.title='Свободные слоты, резервы и расписание юристов';
  }

  const originalOpenCase=openCase;
  const originalAssign=assign;
  function cell(label){return [...cv.querySelectorAll('.grid .cell')].find(node=>node.querySelector('span')?.textContent.trim()===label)}
  function replaceCell(node,label,value){if(node)node.innerHTML='<span>'+e(label)+'</span>'+e(value||'—')}

  // The normal assign action is for an actually unassigned M1 case. Integrity
  // can additionally expose a case whose assigned profile can no longer log in.
  // In that situation a normal auto-assign would be a no-op, so route it through
  // the locked repair endpoint instead of leaving the operator at a dead button.
  assign=async function(id,b){
    let snapshot;
    try{snapshot=await api('/admin/case-workspace/'+id)}catch(x){say('Не удалось проверить текущее назначение: '+(x.message||x),'bad');return}
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
      if(b){b.disabled=false;b.textContent=old}
    }
  };

  openCase=async function(id){
    await originalOpenCase(id);
    if(selected!==id)return;
    let responsibility;
    try{responsibility=await api('/admin/workdesk/cases/'+id+'/responsibility')}catch(_){return}
    if(selected!==id||responsibility.route!=='M2')return;
    replaceCell(cell('Ответственный'),'Юрист консультации',responsibility.lawyer_name||'будет определён выбранным слотом');
    replaceCell(cell('SLA'),'Контроль консультации',responsibility.lawyer_name?'по выбранному слоту':'ожидается выбор слота');
    replaceCell(cell('Срок'),'Время консультации',responsibility.scheduled_at?dt(responsibility.scheduled_at):'ещё не выбрано');
    [...cv.querySelectorAll('.section a,.section button')].forEach(node=>{
      const label=node.textContent.trim();
      if(label==='SLA'||label==='Назначить перед SLA')node.remove();
    });
  };

  // Operational screens link back with ?case_id=. Honor that intent in the
  // canonical Workdesk instead of dropping the operator at a generic overview.
  // Wait for boot() to obtain the personal API token, then open exactly that
  // side card. No mutation is performed by the deep link.
  const requestedCaseId=Number(new URLSearchParams(window.location.search).get('case_id')||0);
  if(Number.isInteger(requestedCaseId)&&requestedCaseId>0){
    let attempts=0;
    const timer=setInterval(()=>{
      attempts+=1;
      if(token){
        clearInterval(timer);
        void openCase(requestedCaseId).then(()=>{
          say('Открыто дело из предыдущего рабочего экрана.','ok');
        }).catch(()=>{});
      }else if(attempts>=40){
        clearInterval(timer);
      }
    },100);
  }
})();
</script>
"""


def _inject_responsibility_patch(html: str) -> str:
    marker = "</body>"
    if html.count(marker) != 1:
        raise RuntimeError(
            "Workdesk template contract changed: </body> marker is not unique"
        )
    if "/admin/workdesk/cases/'+id+'/responsibility" in html:
        raise RuntimeError("Workdesk responsibility patch is already composed")
    return html.replace(marker, _WORKDESK_RESPONSIBILITY_PATCH + marker, 1)


def render_workdesk_html() -> str:
    """Build the one canonical Workdesk document in a deterministic order.

    Route/data modules must not mutate ``WORKDESK_HTML`` themselves.  This
    renderer owns composition of the base UI, responsibility/deep-link behavior
    and process-integrity overlay so that the final staff surface has one
    inspectable construction boundary.
    """

    html = _inject_responsibility_patch(WORKDESK_HTML)
    html = inject_workdesk_integrity(html)
    if html.count('id="processIntegrityBanner"') != 1:
        raise RuntimeError("Workdesk integrity banner composition is not unique")
    return html


__all__ = ["render_workdesk_html"]
