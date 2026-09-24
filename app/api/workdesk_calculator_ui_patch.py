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
  function percent(value){
    if(value===null||value===undefined||value==='')return'—';
    const number=Number(value);
    return Number.isFinite(number)?(Math.round(number*100000)/1000).toLocaleString('ru-RU')+'%':e(value);
  }
  function participant(value){
    if(value==='consumer')return'гражданин для личных нужд';
    if(value==='other')return'иной участник';
    return value?e(value):'не указано';
  }
  function legalSources(calc){
    const sources=Array.isArray(calc?.sources)?calc.sources:[];
    if(!sources.length)return'<div class="muted">Связанные источники в сохранённом расчёте отсутствуют.</div>';
    return '<div class="legal-source-list">'+sources.map(source=>{
      const rawUrl=String(source.url||'');
      const link=rawUrl.startsWith('https://')
        ?'<a href="'+e(rawUrl)+'" target="_blank" rel="noopener noreferrer">Открыть источник ↗</a>'
        :'<span class="bad">Ссылка отсутствует/некорректна</span>';
      const checked=source.checked_at?' · проверено '+e(source.checked_at):'';
      return '<div class="legal-source"><div><code>'+e(source.code||'—')+'</code> · '+e(source.title||'Источник')+checked+'</div><div>'+link+'</div></div>';
    }).join('')+'</div>';
  }
  function chargeSegments(calc){
    const charged=Array.isArray(calc?.applied_segments)?calc.applied_segments:[];
    const excluded=Array.isArray(calc?.excluded_segments)?calc.excluded_segments:[];
    let html='';
    if(charged.length){
      html+='<div class="evidence-subtitle">Начисляемые сегменты</div><div style="overflow:auto"><table class="evidence-table"><thead><tr><th>Период</th><th>Дни</th><th>Базовая ставка</th><th>После cap</th><th>Cap</th><th>Коэф.</th><th>Сумма</th></tr></thead><tbody>'
        +charged.map(item=>'<tr><td>'+calendarDate(item.start)+'–'+calendarDate(item.end)+'</td><td>'+e(item.days??'—')+'</td><td>'+percent(item.base_rate)+'</td><td>'+percent(item.rate)+'</td><td>'+percent(item.cap)+'</td><td>'+e(item.multiplier??'—')+'</td><td>'+optionalMoney(item.amount_before_final_rounding)+'</td></tr>').join('')
        +'</tbody></table></div>';
    }
    if(excluded.length){
      html+='<div class="evidence-subtitle">Исключённые периоды / моратории</div><div style="overflow:auto"><table class="evidence-table"><thead><tr><th>Правило</th><th>Период</th><th>Дни</th><th>Источники</th></tr></thead><tbody>'
        +excluded.map(item=>'<tr><td>'+e(item.code||'—')+'</td><td>'+calendarDate(item.start)+'–'+calendarDate(item.end)+'</td><td>'+e(item.days??'—')+'</td><td>'+e((item.source_refs||[]).join(', ')||'—')+'</td></tr>').join('')
        +'</tbody></table></div>';
    }
    return html||'<div class="muted">Сегменты расчёта отсутствуют.</div>';
  }
  function calculationEvidenceDetails(calc){
    if(!calc)return'';
    const capLine=calc.amount_cap_applied?'<div class="evidence-callout"><b>Применён предельный размер:</b> '+optionalMoney(calc.amount_cap)+'; сумма до ограничения '+optionalMoney(calc.gross_penalty_amount)+'.</div>':'';
    return '<details class="legal-evidence-details"><summary>⚖️ Основания, ставки, периоды и источники</summary>'
      +'<div class="grid evidence-grid"><div class="cell"><span>Ставка на дату исполнения</span>'+percent(calc.key_rate)+'</div><div class="cell"><span>Коэффициент</span>'+e(calc.consumer_multiplier??'—')+'</div><div class="cell"><span>Тип участника</span>'+participant(calc.client_type)+'</div><div class="cell"><span>Уникальный объект</span>'+yesNo(calc.unique_object)+'</div></div>'
      +capLine+chargeSegments(calc)+'<div class="evidence-subtitle">Правовые и расчётные источники</div>'+legalSources(calc)
      +'<div class="muted evidence-integrity">Версия правил: '+e(calc.rule_revision_key||'legacy / не указана')+' · SHA-256 '+e(calc.rule_snapshot_sha256||'—')+'</div></details>';
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
        <div class="cell"><span>Тип участника</span>${participant(intake.client_type)}</div>
        <div class="cell"><span>Уникальный объект</span>${yesNo(intake.unique_object)}</div>
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
      ${calculationEvidenceDetails(calc)}
    `:'';
    return `<div class="section calculator-evidence" data-calculator-evidence><style>.calculator-evidence .legal-evidence-details{margin-top:12px;border:1px solid #e4e7ec;border-radius:12px;padding:10px;background:#fbfcfe}.calculator-evidence .legal-evidence-details summary{cursor:pointer;font-weight:800}.calculator-evidence .evidence-grid{margin-top:10px}.calculator-evidence .evidence-subtitle{font-weight:800;font-size:12px;margin:13px 0 6px}.calculator-evidence .evidence-table{border-collapse:collapse;width:100%;font-size:11px}.calculator-evidence .evidence-table th,.calculator-evidence .evidence-table td{border:1px solid #e4e7ec;padding:6px;text-align:left;vertical-align:top}.calculator-evidence .evidence-table th{background:#f2f4f7}.calculator-evidence .legal-source{border:1px solid #e4e7ec;border-radius:9px;padding:8px;margin:6px 0;font-size:12px}.calculator-evidence .legal-source a{font-weight:700}.calculator-evidence .evidence-callout{margin-top:10px;padding:8px;border-radius:9px;background:#fff7ed;font-size:12px}.calculator-evidence .evidence-integrity{margin-top:10px;word-break:break-all}</style>${source}${result}<div class="muted" style="margin-top:8px">${e(payload.evidence_note||'')}</div></div>`;
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
