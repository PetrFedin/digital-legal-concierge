WORKDESK_CALCULATOR_EXTENSION = r"""
<script>
(function(){
  const calculatorAwareOpenCase=openCase;

  function calendarDate(value){
    if(!value)return'—';
    const raw=String(value).slice(0,10),parts=raw.split('-');
    return parts.length===3?parts.reverse().join('.'):e(value);
  }
  function yesNo(value){
    return value===true?'да':value===false?'нет':'не указано';
  }
  function optionalMoney(value){
    return value===null||value===undefined||value===''?'—':money(value);
  }
  function calculatorSection(payload){
    const intake=payload.intake,calc=payload.latest_calculation;
    if(!intake&&!calc)return'';
    const source=intake?`
      <div class="section-title">Исходные данные клиента</div>
      <div class="grid">
        <div class="cell"><span>Стоимость по ДДУ</span>${optionalMoney(intake.contract_price)}</div>
        <div class="cell"><span>Дата передачи по ДДУ</span>${calendarDate(intake.planned_transfer_date)}</div>
        <div class="cell"><span>Объект передан</span>${yesNo(intake.object_transferred)}</div>
        <div class="cell"><span>Фактическая передача</span>${calendarDate(intake.actual_transfer_date)}</div>
      </div>
      <div class="muted" style="margin-top:7px">Черновик: ${e(intake.status||'—')} · шаг ${e(intake.current_step||'—')} · версия ${e(intake.version||'—')}</div>
    `:'';
    const result=calc?`
      <div class="section-title" style="margin-top:12px">Последний предварительный расчёт</div>
      <div class="grid">
        <div class="cell"><span>Сумма</span>${optionalMoney(calc.penalty_amount)}</div>
        <div class="cell"><span>Дата расчёта</span>${calendarDate(calc.calculation_date)}</div>
        <div class="cell"><span>Просрочка всего</span>${e(calc.delay_days_total??0)} дн.</div>
        <div class="cell"><span>Начисляемые дни</span>${e(calc.delay_days_chargeable??0)} дн.</div>
        <div class="cell"><span>Исключено</span>${e(calc.moratorium_days??0)} дн.</div>
        <div class="cell"><span>Сегменты</span>${e(calc.segment_count??0)}</div>
      </div>
      <div class="muted" style="margin-top:7px">Редакция: ${e(calc.rule_revision_key||'legacy / не указана')} · SHA-256 ${e(calc.rule_snapshot_sha256?String(calc.rule_snapshot_sha256).slice(0,12)+'…':'—')} · расчёт #${e(calc.id)}</div>
    `:'';
    return `<div class="section calculator-evidence" data-calculator-evidence>${source}${result}<div class="muted" style="margin-top:8px">${e(payload.evidence_note||'')}</div></div>`;
  }
  function insertCalculatorSection(html){
    cv.querySelector('[data-calculator-evidence]')?.remove();
    if(!html)return;
    const sectionNode=htmlNode(html);
    const historyTitle=[...cv.querySelectorAll('.section-title')].find(
      node=>node.textContent.trim()==='История дела'
    );
    const historySection=historyTitle?.closest('.section');
    if(historySection)cv.insertBefore(sectionNode,historySection);
    else cv.appendChild(sectionNode);
  }

  openCase=async function(rawId){
    const id=Number(rawId),result=await calculatorAwareOpenCase(rawId);
    if(!Number.isInteger(id)||id<=0||selected!==id||cv.querySelector('.error'))return result;
    try{
      const payload=await api('/admin/workdesk/cases/'+id+'/calculator');
      if(selected!==id)return result;
      insertCalculatorSection(calculatorSection(payload));
    }catch(x){
      if(selected===id){
        insertCalculatorSection(`<div class="section calculator-evidence" data-calculator-evidence><div class="timeline-error">Данные предварительного расчёта не загружены: ${e(x.message||x)}.</div></div>`);
      }
    }
    return result;
  };
})();
</script>
"""

__all__ = ["WORKDESK_CALCULATOR_EXTENSION"]
