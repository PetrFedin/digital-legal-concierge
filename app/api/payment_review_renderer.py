from __future__ import annotations

from app.api.payment_review_center import PAYMENT_REVIEW_CENTER_HTML


_PAYMENT_REVIEW_REASON = '<div class="reason"><b>Почему автоматика остановилась</b><br>'
_PAYMENT_REVIEW_REASON_GUIDED = (
    '<div class="reason"><b>Сейчас · почему требуется сверка</b><br>'
)
_PAYMENT_REVIEW_NEXT = '<div class="next"><b>Что делать</b><br>'
_PAYMENT_REVIEW_NEXT_GUIDED = '<div class="next"><b>Главный следующий шаг</b><br>'
_PAYMENT_REVIEW_SECONDARY = '<div style="margin-top:10px">${x.case_detail_url?'
_PAYMENT_REVIEW_SECONDARY_GUIDED = (
    '<div style="margin-top:10px"><div class="muted" style="margin-bottom:6px">'
    '<b>Вторичные действия</b></div>${x.case_detail_url?'
)
_PAYMENT_REVIEW_SUBTITLE = (
    '<div style="font-size:12px;color:#d0d5dd">'
    'Деньги получены, но автоматическое действие остановлено безопасностью</div>'
)
_PAYMENT_REVIEW_SUBTITLE_GUIDED = (
    '<div id="paymentReviewContext" style="font-size:12px;color:#d0d5dd">'
    'Роль: администратор · время загружается…</div>'
)
_PAYMENT_REVIEW_BOOT = (
    "businessTimeZone=s.business_timezone||businessTimeZone;"
    "businessTimeLabel=s.business_timezone_label??businessTimeLabel;"
)
_PAYMENT_REVIEW_BOOT_GUIDED = (
    "businessTimeZone=s.business_timezone||businessTimeZone;"
    "businessTimeLabel=s.business_timezone_label??businessTimeLabel;"
    "paymentReviewContext.textContent=`Роль: администратор · время: ${businessTimeLabel||businessTimeZone}`;"
)
_HISTORY_MAIN_MARKER = '<div id="message" class="muted" role="status" aria-live="polite"></div>\n</main>'
_HISTORY_SCRIPT_MARKER = "boot();\n</script>"
_HISTORY_STYLE_MARKER = "</style>"
_HISTORY_PANEL = r"""
<section id="paymentReviewHistory" class="review review-history" hidden aria-live="polite">
  <div class="review-head">
    <div>
      <h2>История сверки</h2>
      <div class="muted">Только подтверждённые факты по выбранному платежу. Комментарии и технические данные журнала здесь не раскрываются.</div>
    </div>
    <span class="badge" id="paymentReviewHistoryStatus">История</span>
  </div>
  <div id="paymentReviewHistoryBody" class="history-list">Загрузка истории…</div>
</section>
""".strip()
_HISTORY_STYLE = r"""
.review-history{margin-top:14px}.history-list{display:grid;gap:9px;margin-top:12px}.history-item{border:1px solid var(--line);border-radius:12px;padding:11px;background:#f8fafc}.history-item-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.history-item-title{font-weight:800}.history-facts{display:flex;gap:7px;flex-wrap:wrap;margin-top:7px}.history-fact{background:#fff;border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:12px}.history-reason{margin-top:8px;line-height:1.4}.history-meta{margin-top:8px;color:var(--muted);font-size:12px}
""".strip()
_HISTORY_SCRIPT = r"""
async function loadReviewHistory(){
  const panel=document.getElementById('paymentReviewHistory'),body=document.getElementById('paymentReviewHistoryBody'),badge=document.getElementById('paymentReviewHistoryStatus');
  if(!panel||!body||!badge)return;
  if(!requestedPaymentId){panel.hidden=true;return}
  panel.hidden=false;
  badge.textContent='Загрузка…';
  body.innerHTML='<div class="muted">Загрузка истории платежа…</div>';
  try{
    const history=await api('/admin/payment-reviews/'+requestedPaymentId+'/history');
    terminalCaseId=Number(history.case_id)||terminalCaseId;
    badge.textContent=history.payment_status||'Статус неизвестен';
    const events=Array.isArray(history.events)?history.events:[];
    if(!events.length){
      body.innerHTML='<div class="empty">По этому платежу нет событий сверки в журнале.</div>';
      return;
    }
    const items=events.map(event=>{
      const required=event.kind==='required';
      const title=required?'Сверка создана':'Сверка завершена';
      const facts=[];
      if(event.origin_status)facts.push('До: '+esc(event.origin_status));
      if(event.resulting_status)facts.push('После: '+esc(event.resulting_status));
      if(!required&&event.decision)facts.push('Решение: '+esc(decisionLabel(event.decision)));
      if(event.consultation_id)facts.push('Консультация #'+esc(event.consultation_id));
      if(event.slot_id)facts.push('Слот #'+esc(event.slot_id));
      if(event.orphan_consultation_id)facts.push('Отсутствующая консультация #'+esc(event.orphan_consultation_id));
      if(event.orphan_slot_id)facts.push('Исходный слот #'+esc(event.orphan_slot_id));
      const actor=event.actor_id?`${esc(event.actor_type||'staff')} #${esc(event.actor_id)}`:esc(event.actor_type||'system');
      const reason=required&&event.reason?`<div class="history-reason"><b>Причина:</b> ${esc(event.reason)}</div>`:'';
      return `<div class="history-item"><div class="history-item-head"><div class="history-item-title">${title}</div><div class="muted">${esc(formatDate(event.created_at))}</div></div><div class="history-facts">${facts.map(f=>`<span class="history-fact">${f}</span>`).join('')}</div>${reason}<div class="history-meta">Источник: защищённый журнал · действие: ${actor}</div></div>`;
    }).join('');
    const truncated=history.truncated?`<div class="muted">Показаны последние ${esc(history.event_count)} из ${esc(history.total_event_count)} событий этого платежа.</div>`:'';
    body.innerHTML=items+truncated;
  }catch(error){
    if(error.status===404){
      badge.textContent='Не найден';
      body.innerHTML='<div class="empty">Платёж не найден или история больше недоступна.</div>';
      return;
    }
    badge.textContent='Ошибка';
    body.innerHTML=`<div class="bad">Не удалось загрузить историю: ${esc(error.message||error)}</div>`;
  }
}
const paymentReviewBaseLoad=load;
load=async function(){
  await paymentReviewBaseLoad();
  await loadReviewHistory();
};
""".strip()


def _inject_guided_copy(html: str) -> str:
    """Apply the canonical staff hierarchy at one deterministic render boundary."""

    markers = (
        _PAYMENT_REVIEW_REASON,
        _PAYMENT_REVIEW_NEXT,
        _PAYMENT_REVIEW_SECONDARY,
        _PAYMENT_REVIEW_SUBTITLE,
        _PAYMENT_REVIEW_BOOT,
    )
    if any(html.count(marker) != 1 for marker in markers):
        raise RuntimeError(
            "Payment Review template contract changed: guided UI markers not found exactly once"
        )
    return (
        html.replace(_PAYMENT_REVIEW_REASON, _PAYMENT_REVIEW_REASON_GUIDED, 1)
        .replace(_PAYMENT_REVIEW_NEXT, _PAYMENT_REVIEW_NEXT_GUIDED, 1)
        .replace(_PAYMENT_REVIEW_SECONDARY, _PAYMENT_REVIEW_SECONDARY_GUIDED, 1)
        .replace(_PAYMENT_REVIEW_SUBTITLE, _PAYMENT_REVIEW_SUBTITLE_GUIDED, 1)
        .replace(_PAYMENT_REVIEW_BOOT, _PAYMENT_REVIEW_BOOT_GUIDED, 1)
    )


def _inject_history_ui(html: str) -> str:
    """Compose the read-only exact-payment history into active and terminal deep links."""

    markers = (_HISTORY_MAIN_MARKER, _HISTORY_SCRIPT_MARKER)
    if any(html.count(marker) != 1 for marker in markers) or html.count(_HISTORY_STYLE_MARKER) != 1:
        raise RuntimeError(
            "Payment Review template contract changed: history composition markers are not unique"
        )
    if 'id="paymentReviewHistory"' in html or "loadReviewHistory" in html:
        raise RuntimeError("Payment Review history UI is already composed")
    html = html.replace(
        _HISTORY_STYLE_MARKER,
        _HISTORY_STYLE + "\n" + _HISTORY_STYLE_MARKER,
        1,
    )
    html = html.replace(
        _HISTORY_MAIN_MARKER,
        '<div id="message" class="muted" role="status" aria-live="polite"></div>\n'
        + _HISTORY_PANEL
        + "\n</main>",
        1,
    )
    return html.replace(
        _HISTORY_SCRIPT_MARKER,
        _HISTORY_SCRIPT + "\nboot();\n</script>",
        1,
    )


def render_payment_review_html() -> str:
    """Build the protected Payment Review document with one inspectable owner."""

    html = _inject_guided_copy(PAYMENT_REVIEW_CENTER_HTML)
    html = _inject_history_ui(html)
    if html.count('id="paymentReviewHistory"') != 1:
        raise RuntimeError("Payment Review history composition is not unique")
    return html


__all__ = ["render_payment_review_html"]
