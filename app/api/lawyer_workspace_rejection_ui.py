from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.api.guided_lawyer_ui import _WORKSPACE_DEEP_LINK_PATCH, _inject_patch
from app.api.lawyer_consultation_decision_guard import (
    router as lawyer_consultation_decision_guard_router,
)
from app.api.lawyer_m1_rejection import router as lawyer_m1_rejection_router
from app.api.lawyer_poa import router as lawyer_poa_router
from app.api.lawyer_workspace import WORKSPACE_HTML

router = APIRouter(tags=["lawyer-workspace-rejection-ui"])
# This composite router is mounted before guided/legacy lawyer routers in
# create_app(). Keep mutation guards first so duplicated historical endpoints
# can never win route precedence.
router.include_router(lawyer_m1_rejection_router)
router.include_router(lawyer_consultation_decision_guard_router)
router.include_router(lawyer_poa_router)


_M1_REJECTION_PATCH = r"""
<script>
(function(){
  configs.reject_m1={
    title:'Отказать в полном ведении M1',
    min:10,
    effect:'После подтверждения M1 не будет принят в полное ведение. Дело не закроется автоматически: клиент получит выбор — перейти в консультацию или завершить обращение.'
  };

  const legacyTypeAvailable=typeAvailable;
  typeAvailable=function(x,type){
    if(type==='reject_m1')return x?.route==='M1'&&['M1_LAWYER_REVIEW','M1_DOCS_REQUESTED'].includes(String(x.status||''));
    return legacyTypeAvailable(x,type);
  };

  const guidedCaseCard=caseCard;
  caseCard=function(x){
    let html=guidedCaseCard(x);
    if(!typeAvailable(x,'reject_m1'))return html;
    const id=Number(x.case_id);
    const block=`<div class="deadline"><b>Решение по полному ведению M1</b><div class="muted">Если полное ведение не подходит, зафиксируйте отказ с причиной. Это отличается от «Перевести в консультацию»: при отказе маршрут не выбирается за клиента — он сам решит, продолжить как M2 или закрыть обращение.</div><div class="actions" style="margin-top:9px"><button class="red" data-case-id="${id}" onclick="openCaseForm(${id},'reject_m1')">Отказать в полном ведении</button></div></div>`;
    return html.replace('</article>',block+'</article>');
  };

  const legacySubmitCaseForm=submitCaseForm;
  submitCaseForm=async function(id,button){
    const form=document.getElementById('case_form_'+id),type=form?.dataset.type||'';
    if(type!=='reject_m1')return legacySubmitCaseForm(id,button);

    const x=caseSnapshot(id),comment=(document.getElementById('case_comment_'+id)?.value||'').trim();
    if(!x||!typeAvailable(x,type)){showCaseError(id,'Карточка дела изменилась. Черновик сохранён.',true);return}
    if(form.dataset.stage!=='review'){showCaseError(id,'Сначала проверьте действие перед сохранением.');return}
    if(comment.length<configs.reject_m1.min){showCaseError(id,'Причина отказа должна содержать не менее 10 символов.');backToCaseEdit(id);return}
    caseDrafts.set(draftKey(id,type),comment);

    return withAction(`case:${id}`,caseControls(id),button,async()=>{
      try{
        await api(`/lawyer/cases/${id}/reject`,{
          method:'POST',
          body:JSON.stringify({reason:comment,expected_status:x.status,expected_updated_at:x.updated_at})
        });
      }catch(e){
        if(e.status===409){
          caseDrafts.set(draftKey(id,type),comment);
          showCaseError(id,'Карточка дела изменилась. Причина отказа сохранена как черновик.',true);
          feedback('Отказ не записан: сначала обновите карточку и проверьте решение ещё раз.','warn-text');
        }else{
          showCaseError(id,e.message);
          feedback('Отказ не сохранён: '+e.message,'bad');
        }
        return;
      }

      caseDrafts.delete(draftKey(id,type));
      try{
        await load(null,true);
      }catch(e){
        feedback('Отказ сохранён, клиент получил следующий выбор, но кабинет не обновился: '+e.message,'warn-text');
        return;
      }
      feedback('Отказ в полном ведении сохранён. Клиенту открыт выбор: консультация или завершение обращения.','ok');
    });
  };
})();
</script>
"""


_M1_POA_PATCH = r"""
<script>
(function(){
  configs.confirm_poa={
    title:'Подтвердить получение доверенности',
    min:5,
    effect:'После подтверждения система зафиксирует фактическое получение доверенности назначенным юристом. Только после этого станет доступна подготовка претензии.'
  };

  const previousTypeAvailable=typeAvailable;
  typeAvailable=function(x,type){
    if(type==='confirm_poa')return x?.route==='M1'&&String(x.status||'')==='M1_POWER_OF_ATTORNEY';
    return previousTypeAvailable(x,type);
  };

  const previousPrimaryButton=primaryButton;
  primaryButton=function(x){
    if(typeAvailable(x,'confirm_poa')){
      return `<button class="green" data-case-id="${x.case_id}" onclick="openCaseForm(${x.case_id},'confirm_poa')">Подтвердить получение доверенности</button>`;
    }
    return previousPrimaryButton(x);
  };

  const previousCaseCard=caseCard;
  caseCard=function(x){
    let html=previousCaseCard(x);
    if(!typeAvailable(x,'confirm_poa'))return html;
    const id=Number(x.case_id);
    const block=`<div class="deadline"><b>Контроль доверенности</b><div class="muted">Сообщение клиента «доверенность оформлена» не считается фактом получения. Проверьте фактическую передачу/получение и только затем подтвердите этап. До подтверждения подготовка претензии не откроется.</div><div class="actions" style="margin-top:9px"><button class="green" data-case-id="${id}" onclick="openCaseForm(${id},'confirm_poa')">Подтвердить фактическое получение</button><a class="button secondary" href="/document-access/ui?case_id=${id}">Материалы дела</a><a class="button secondary" href="/message-center/ui?case_id=${id}">Связаться с клиентом</a></div></div>`;
    return html.replace('</article>',block+'</article>');
  };

  const previousSubmitCaseForm=submitCaseForm;
  submitCaseForm=async function(id,button){
    const form=document.getElementById('case_form_'+id),type=form?.dataset.type||'';
    if(type!=='confirm_poa')return previousSubmitCaseForm(id,button);

    const x=caseSnapshot(id),comment=(document.getElementById('case_comment_'+id)?.value||'').trim();
    if(!x||!typeAvailable(x,type)){showCaseError(id,'Карточка дела изменилась. Черновик сохранён.',true);return}
    if(form.dataset.stage!=='review'){showCaseError(id,'Сначала проверьте действие перед сохранением.');return}
    if(comment.length<configs.confirm_poa.min){showCaseError(id,'Укажите, как подтверждено получение доверенности — минимум 5 символов.');backToCaseEdit(id);return}
    caseDrafts.set(draftKey(id,type),comment);

    return withAction(`case:${id}`,caseControls(id),button,async()=>{
      try{
        await api(`/lawyer/cases/${id}/poa/received`,{
          method:'POST',
          body:JSON.stringify({comment,expected_status:x.status,expected_updated_at:x.updated_at})
        });
      }catch(e){
        if(e.status===409){
          caseDrafts.set(draftKey(id,type),comment);
          showCaseError(id,'Карточка дела изменилась. Подтверждение не записано, черновик сохранён.',true);
          feedback('Получение доверенности не подтверждено. Обновите карточку и проверьте фактический статус.','warn-text');
        }else{
          showCaseError(id,e.message);
          feedback('Подтверждение не сохранено: '+e.message,'bad');
        }
        return;
      }

      caseDrafts.delete(draftKey(id,type));
      try{
        await load(null,true);
      }catch(e){
        feedback('Получение доверенности подтверждено, но кабинет не обновился: '+e.message,'warn-text');
        return;
      }
      feedback('Получение доверенности подтверждено. Теперь доступна подготовка претензии.','ok');
    });
  };
})();
</script>
"""


def enhanced_lawyer_workspace_html() -> str:
    return _inject_patch(
        WORKSPACE_HTML,
        _WORKSPACE_DEEP_LINK_PATCH + _M1_REJECTION_PATCH + _M1_POA_PATCH,
    )


@router.get("/lawyer/workspace/ui", response_class=HTMLResponse)
async def lawyer_workspace_with_rejection_ui():
    return HTMLResponse(enhanced_lawyer_workspace_html())
