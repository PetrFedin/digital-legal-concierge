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


_COURT_DECISION_PATCH = r"""
<script>
(function(){
  const courtReferenceDrafts=new Map(),courtDateDrafts=new Map();
  if(configs.open_court_payment){
    configs.open_court_payment.effect='Система сначала сохранит дату и идентификатор судебного акта в аудите. Только после этого откроется второй платёж 70 000 ₽.';
  }

  function ensureCourtEvidence(id){
    let block=document.getElementById('court_evidence_'+id);
    if(block)return block;
    const label=document.getElementById('case_comment_label_'+id);
    if(!label)return null;
    block=document.createElement('div');
    block.id='court_evidence_'+id;
    block.style.display='none';
    block.innerHTML=`<label class="label" for="court_date_${id}">Дата судебного акта</label><input id="court_date_${id}" type="date" oninput="this.dataset.touched='1'"><label class="label" for="court_reference_${id}">Номер дела / решения / идентификатор акта</label><input id="court_reference_${id}" autocomplete="off" maxlength="240" placeholder="Например: А40-12345/2026, решение от 12.08.2026"><div class="muted" style="margin:7px 0 10px">Не открывайте платёж по промежуточному событию. Укажите акт, после которого второй платёж действительно наступил по договору.</div>`;
    label.parentNode.insertBefore(block,label);
    return block;
  }

  function courtEvidence(id,showError=true){
    const reference=(document.getElementById('court_reference_'+id)?.value||'').trim();
    const decisionDate=(document.getElementById('court_date_'+id)?.value||'').trim();
    if(reference.length<5){if(showError)showCaseError(id,'Укажите номер дела, решения или другой идентификатор судебного акта — минимум 5 символов.');return null}
    if(!/^\d{4}-\d{2}-\d{2}$/.test(decisionDate)){if(showError)showCaseError(id,'Укажите дату судебного акта.');return null}
    const today=new Date();today.setHours(23,59,59,999);const parsed=new Date(decisionDate+'T00:00:00');
    if(Number.isNaN(parsed.getTime())||parsed>today){if(showError)showCaseError(id,'Дата судебного акта не может быть в будущем.');return null}
    courtReferenceDrafts.set(id,reference);courtDateDrafts.set(id,decisionDate);
    return {reference,decisionDate};
  }

  const previousOpenCaseForm=openCaseForm;
  openCaseForm=function(id,type){
    previousOpenCaseForm(id,type);
    const block=ensureCourtEvidence(id);
    if(!block)return;
    block.style.display=type==='open_court_payment'?'block':'none';
    if(type==='open_court_payment'){
      const ref=document.getElementById('court_reference_'+id),date=document.getElementById('court_date_'+id);
      ref.value=courtReferenceDrafts.get(id)||'';
      date.value=courtDateDrafts.get(id)||'';
      ref.oninput=()=>courtReferenceDrafts.set(id,ref.value);
      date.oninput=()=>courtDateDrafts.set(id,date.value);
      const commentLabel=document.getElementById('case_comment_label_'+id);
      if(commentLabel)commentLabel.textContent='Что произошло и почему по этому акту наступает второй платёж';
    }
  };

  const previousReviewCaseForm=reviewCaseForm;
  reviewCaseForm=function(id){
    const form=document.getElementById('case_form_'+id),type=form?.dataset.type||'';
    if(type!=='open_court_payment')return previousReviewCaseForm(id);
    const evidence=courtEvidence(id,true);if(!evidence)return;
    previousReviewCaseForm(id);
    if(form.dataset.stage==='review'){
      const effect=document.getElementById('case_review_effect_'+id);
      if(effect)effect.textContent=(configs.open_court_payment.effect||'')+` Судебный акт: ${evidence.reference}, дата ${evidence.decisionDate}.`;
    }
  };

  const previousSubmitCaseForm=submitCaseForm;
  submitCaseForm=async function(id,button){
    const form=document.getElementById('case_form_'+id),type=form?.dataset.type||'';
    if(type!=='open_court_payment')return previousSubmitCaseForm(id,button);

    const x=caseSnapshot(id),comment=(document.getElementById('case_comment_'+id)?.value||'').trim(),evidence=courtEvidence(id,true);
    if(!x||!typeAvailable(x,type)){showCaseError(id,'Карточка дела изменилась. Черновик сохранён.',true);return}
    if(form.dataset.stage!=='review'){showCaseError(id,'Сначала проверьте судебный акт и действие перед сохранением.');return}
    if(comment.length<(configs.open_court_payment?.min||5)){showCaseError(id,'Опишите судебный результат или основание открытия второго платежа.');backToCaseEdit(id);return}
    if(!evidence){backToCaseEdit(id);return}
    caseDrafts.set(draftKey(id,type),comment);

    return withAction(`case:${id}`,caseControls(id),button,async()=>{
      try{
        await api(`/lawyer/cases/${id}/court/payment/open`,{
          method:'POST',
          body:JSON.stringify({comment,decision_reference:evidence.reference,decision_date:evidence.decisionDate,expected_status:x.status,expected_updated_at:x.updated_at})
        });
      }catch(e){
        if(e.status===409){
          caseDrafts.set(draftKey(id,type),comment);courtReferenceDrafts.set(id,evidence.reference);courtDateDrafts.set(id,evidence.decisionDate);
          showCaseError(id,'Карточка или судебные данные изменились. Ничего не сохранено; черновик оставлен.',true);
          feedback('Второй платёж не открыт. Обновите карточку и снова проверьте судебный акт.','warn-text');
        }else{
          showCaseError(id,e.message);feedback('Второй платёж не открыт: '+e.message,'bad');
        }
        return;
      }
      caseDrafts.delete(draftKey(id,type));courtReferenceDrafts.delete(id);courtDateDrafts.delete(id);
      try{await load(null,true)}catch(e){feedback('Судебный акт сохранён и второй платёж открыт, но кабинет не обновился: '+e.message,'warn-text');return}
      feedback('Судебный акт зафиксирован. Второй платёж 70 000 ₽ открыт клиенту.','ok');
    });
  };
})();
</script>
"""


def enhanced_lawyer_workspace_html() -> str:
    return _inject_patch(
        WORKSPACE_HTML,
        (
            _WORKSPACE_DEEP_LINK_PATCH
            + _M1_REJECTION_PATCH
            + _M1_POA_PATCH
            + _COURT_DECISION_PATCH
        ),
    )


@router.get("/lawyer/workspace/ui", response_class=HTMLResponse)
async def lawyer_workspace_with_rejection_ui():
    return HTMLResponse(enhanced_lawyer_workspace_html())
