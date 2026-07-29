from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["web-admin"])


@router.get("/admin-ui", response_class=HTMLResponse)
async def admin_ui():
    return HTMLResponse(ADMIN_HTML)


ADMIN_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Digital Legal Concierge — Admin v25</title>
  <style>
    :root { --bg:#f5f6fa; --card:#fff; --text:#111827; --muted:#6b7280; --line:#e5e7eb; --blue:#2563eb; --green:#16a34a; --red:#dc2626; --yellow:#ca8a04; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif; background:var(--bg); color:var(--text); }
    header { padding:16px 22px; background:#111827; color:white; display:flex; align-items:center; justify-content:space-between; gap:16px; position:sticky; top:0; z-index:5; }
    header h1 { margin:0; font-size:18px; }
    header input { width:340px; max-width:55vw; padding:10px 12px; border-radius:10px; border:1px solid #374151; background:#030712; color:white; }
    main { padding:22px; display:grid; gap:16px; }
    .grid { display:grid; grid-template-columns:repeat(7,minmax(0,1fr)); gap:12px; }
    .layout { display:grid; grid-template-columns:1.3fr .9fr; gap:16px; align-items:start; }
    .card { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:16px; box-shadow:0 1px 2px rgba(0,0,0,.04); }
    .metric { font-size:28px; font-weight:800; margin-top:6px; }
    .muted { color:var(--muted); font-size:13px; }
    .tabs { display:flex; flex-wrap:wrap; gap:8px; }
    button { border:0; border-radius:10px; padding:9px 12px; background:var(--blue); color:white; cursor:pointer; font-weight:650; }
    button.secondary { background:#e5e7eb; color:#111827; }
    button.green { background:var(--green); }
    button.red { background:var(--red); }
    button.yellow { background:var(--yellow); }
    table { width:100%; border-collapse:collapse; font-size:14px; }
    th,td { border-bottom:1px solid var(--line); text-align:left; padding:9px 8px; vertical-align:top; }
    th { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.04em; }
    input,select,textarea { padding:9px 10px; border:1px solid var(--line); border-radius:10px; width:100%; }
    textarea { min-height:70px; }
    .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
    .row > * { flex:1; }
    .pill { display:inline-block; padding:4px 8px; border-radius:999px; background:#eef2ff; color:#3730a3; font-size:12px; font-weight:700; }
    .actions { display:flex; flex-wrap:wrap; gap:6px; }
    pre { white-space:pre-wrap; background:#0b1020; color:#d1e7ff; border-radius:12px; padding:12px; max-height:260px; overflow:auto; }
    @media (max-width: 1400px) { .grid { grid-template-columns:repeat(4,1fr); } }
    @media (max-width: 1000px) { .grid { grid-template-columns:repeat(2,1fr); } .layout { grid-template-columns:1fr; } }
    @media (max-width: 560px) { .grid { grid-template-columns:1fr; } header { flex-direction:column; align-items:flex-start; } header input { max-width:100%; width:100%; } }
  </style>
</head>
<body>
  <header>
    <h1>⚖ Digital Legal Concierge — Admin v25</h1><div><a style="color:white;margin-right:12px" href="/login">Вход</a><form style="display:inline" method="post" action="/logout"><button style="background:#374151">Выход</button></form></div>
    <input id="token" type="hidden" />
  </header>
  <main>
    <section class="tabs">
      <button onclick="loadDashboard()">Дашборд</button>
      <button onclick="loadCases()" class="secondary">Дела</button>
      <button onclick="loadQueue()" class="secondary">Очередь</button>
      <button onclick="window.location.href='/admin/sla/ui'" class="red">SLA и просрочки</button>
      <button onclick="loadPayments()" class="secondary">Оплаты</button>
      <button onclick="window.location.href='/admin/payment-reviews/ui'" class="yellow">Проверка оплат</button>
      <button onclick="window.location.href='/admin/refunds/ui'" class="yellow">Возвраты</button>
      <button onclick="window.location.href='/admin/consultation-outcomes/ui'" class="yellow">Контроль встреч</button>
      <button onclick="window.location.href='/lawyer/ui'" class="secondary">Кабинет юриста</button>
      <button onclick="loadDocuments()" class="secondary">Документы</button>
      <button onclick="loadLawyers()" class="secondary">Юристы</button>
      <button onclick="window.location.href='/consultation-slots/ui'" class="secondary">Слоты консультаций</button>
      <button onclick="loadSettings()" class="secondary">Настройки</button>
      <button onclick="window.location.href='/access/ui'" class="secondary">Пользователи и роли</button>
      <button onclick="loadNotifications()" class="secondary">Уведомления</button>
      <button onclick="loadExports()" class="secondary">Экспорт</button>
      <button onclick="loadReady()" class="green">Готовность</button><button onclick="window.location.href='/operator'" class="green">Оператор</button><button onclick="runScheduler()" class="green">Проверки</button>
    </section>

    <section id="dashboard" class="grid"></section>
    <section class="layout">
      <div class="card"><div id="content">Загрузка...</div></div>
      <div class="card"><div id="side"><b>Рабочая область</b><p class="muted">Откройте дело, чтобы увидеть быстрые действия.</p></div></div>
    </section>
    <section class="card"><div class="muted">Технический ответ</div><pre id="raw"></pre></section>
  </main>
<script>
document.getElementById('token').value = localStorage.getItem('admin_token') || '';
async function loadSession(){const r=await fetch('/auth/session'); if(!r.ok){location.href='/login'; return;} const s=await r.json(); document.getElementById('token').value=s.api_token; localStorage.setItem('admin_token',s.api_token);}

const api = async (path, opts={}) => {
  const token = document.getElementById('token').value || 'dev-admin-token';
  const res = await fetch(path, { ...opts, headers: { 'x-admin-token': token, 'Content-Type':'application/json', ...(opts.headers||{}) }});
  const data = await res.json().catch(() => ({}));
  document.getElementById('raw').textContent = JSON.stringify(data, null, 2);
  if (!res.ok) throw new Error(data.detail || 'Ошибка запроса');
  return data;
};
function esc(v) { return String(v ?? '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s])); }
function metric(title, value, note='') { return `<div class="card"><div class="muted">${title}</div><div class="metric">${value}</div><div class="muted">${note}</div></div>`; }
function table(rows, cols, extra=''){
  if (!rows.length) return '<p class="muted">Нет данных.</p>';
  return `<table><thead><tr>${cols.map(c=>`<th>${c}</th>`).join('')}${extra?'<th>Действия</th>':''}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(c=>`<td>${esc(r[c])}</td>`).join('')}${extra?`<td>${extra.replaceAll('__ID__', esc(r.id))}</td>`:''}</tr>`).join('')}</tbody></table>`;
}
async function loadDashboard(){
  const d = await api('/admin/dashboard');
  document.getElementById('dashboard').innerHTML = metric('Новые дела', d.new_cases) + metric('Активные дела', d.active_cases) + metric('SLA просрочен', d.sla_overdue, 'требуют эскалации') + metric('Ожидают оплату', d.waiting_payment) + metric('Проверка оплат', d.payment_reviews, 'PAID_REVIEW') + metric('Консультации', d.consultations_booked) + metric('Закрытые', d.closed_cases);
  document.getElementById('content').innerHTML = '<h3>Дашборд</h3><p>Просроченные юридические действия контролируются через «SLA и просрочки». Финансовые исключения обрабатываются через «Проверка оплат» и «Возвраты», а завершение и неявки — через «Контроль встреч».</p>';
}
async function loadCases(){
  const rows = await api('/admin/cases');
  document.getElementById('content').innerHTML = '<h3>Дела</h3>'+table(rows, ['id','number','route','status','lawyer_id','next_action'], '<button onclick="openCase(__ID__)">Открыть</button>');
}
async function loadQueue(){
  const rows = await api('/admin/queue');
  document.getElementById('content').innerHTML = '<h3>Очередь без юриста</h3>'+table(rows, ['id','number','route','status','next_action'], '<button onclick="autoAssign(__ID__)" class="green">Автоназначить</button> <button onclick="openCase(__ID__)">Открыть</button>');
}
async function openCase(id){
  const d = await api('/admin/cases/'+id);
  const c = d.case, cl = d.client || {};
  document.getElementById('side').innerHTML = `<h3>Дело ${esc(c.number)}</h3><p><span class="pill">${esc(c.route||'—')}</span> <span class="pill">${esc(c.status)}</span></p><p><b>Клиент:</b><br>${esc(cl.name)}<br>@${esc(cl.username)}<br>TG: ${esc(cl.telegram_id)}</p><p><b>Следующий шаг:</b><br>${esc(c.next_action)}</p><h4>Быстрые действия</h4><div class="actions"><button class="green" onclick="autoAssign(${id})">Автоназначить</button><button onclick="setStatus(${id})">Сменить статус</button><button class="yellow" onclick="openPaymentsForCase(${id})">Оплаты</button><button class="red" onclick="window.location.href='/admin/sla/ui'">SLA</button></div>`;
  const pay = d.payments.map(p=>`<tr><td>${p.id}</td><td>${esc(p.title)}</td><td>${p.amount}</td><td>${esc(p.status)}</td><td>${p.status==='PAID_REVIEW'?`<button onclick="window.location.href='/admin/payment-reviews/ui'" class="yellow">Проверить</button>`:p.manual_confirm_allowed?`<button onclick="confirmPayment(${p.id}, ${id})" class="green">DEV подтвердить</button>`:'—'}</td></tr>`).join('') || '<tr><td colspan="5">Нет платежей</td></tr>';
  const docs = d.documents.map(x=>`<tr><td>${x.id}</td><td>${esc(x.title)}</td><td>${esc(x.file_name)}</td><td>${esc(x.status)}</td><td>v${x.version}</td></tr>`).join('') || '<tr><td colspan="5">Нет документов</td></tr>';
  document.getElementById('content').innerHTML = `<h3>Карточка дела</h3><p><b>${esc(c.number)}</b></p><h4>Платежи</h4><table><tr><th>ID</th><th>Название</th><th>Сумма</th><th>Статус</th><th>Действие</th></tr>${pay}</table><h4>Документы</h4><table><tr><th>ID</th><th>Тип</th><th>Файл</th><th>Статус</th><th>Версия</th></tr>${docs}</table>`;
}
async function setStatus(id){
  const statuses = await api('/admin/statuses');
  document.getElementById('side').innerHTML += `<div class="card"><h4>Смена статуса</h4><select id="status_${id}">${statuses.map(s=>`<option>${esc(s)}</option>`).join('')}</select><textarea id="comment_${id}" placeholder="Комментарий"></textarea><button onclick="saveStatus(${id})">Сохранить статус</button></div>`;
}
async function saveStatus(id){ await api('/admin/cases/'+id+'/status',{method:'POST', body:JSON.stringify({status:document.getElementById('status_'+id).value, comment:document.getElementById('comment_'+id).value})}); await openCase(id); }
async function autoAssign(id){ await api('/admin/cases/'+id+'/auto-assign',{method:'POST'}); await openCase(id); }
async function confirmPayment(pid, cid){ if(!confirm('Подтвердить fake-платёж в тестовой среде?'))return; await api('/admin/payments/'+pid+'/confirm',{method:'POST'}); if (cid && cid > 0) { await openCase(cid); } else { await loadPayments(); } }
async function openPaymentsForCase(id){ await openCase(id); }
async function loadPayments(){
  const rows = await api('/admin/payments');
  const body = rows.map(p=>`<tr><td>${p.id}</td><td>${p.case_id}</td><td>${esc(p.code)}</td><td>${esc(p.title)}</td><td>${p.amount}</td><td>${esc(p.status)}</td><td>${esc(p.provider||'—')}</td><td>${p.status==='PAID_REVIEW'?`<button onclick="window.location.href='/admin/payment-reviews/ui'" class="yellow">Проверить</button>`:p.manual_confirm_allowed?`<button onclick="confirmPayment(${p.id},0)" class="green">DEV подтвердить</button>`:'—'}</td></tr>`).join('') || '<tr><td colspan="8">Нет платежей</td></tr>';
  document.getElementById('content').innerHTML = `<h3>Оплаты</h3><p class="muted">Production-платежи подтверждаются провайдером. Полученные деньги без подтверждённого слота обрабатываются только через центр «Проверка оплат».</p><table><tr><th>ID</th><th>Дело</th><th>Код</th><th>Название</th><th>Сумма</th><th>Статус</th><th>Провайдер</th><th>Действие</th></tr>${body}</table>`;
}
async function loadDocuments(){ const rows = await api('/admin/documents'); document.getElementById('content').innerHTML = '<h3>Документы</h3>'+table(rows, ['id','case_id','type','title','file_name','status','version']); }
async function loadLawyers(){ const rows = await api('/admin/lawyers'); document.getElementById('content').innerHTML = '<h3>Юристы</h3>'+table(rows, ['id','full_name','email','is_active','workload_limit'])+`<h4>Добавить юриста</h4><div class="row"><input id="lw_name" placeholder="ФИО"><input id="lw_email" placeholder="email"><button onclick="createLawyer()">Создать</button></div>`; }
async function createLawyer(){ await api('/admin/lawyers',{method:'POST', body:JSON.stringify({full_name:document.getElementById('lw_name').value,email:document.getElementById('lw_email').value})}); await loadLawyers(); }
async function loadSettings(){ const rows = await api('/admin/settings'); document.getElementById('content').innerHTML = '<h3>Настройки</h3>'+rows.map(r => `<div class="card"><b>${esc(r.title)}</b><div class="muted">${esc(r.key)}</div><div class="row"><input id="set_${esc(r.key)}" value="${esc(r.value?.value)}"><button onclick="saveSetting('${esc(r.key)}')">Сохранить</button></div></div>`).join(''); }
async function saveSetting(key){ const value = document.getElementById('set_'+key).value; const normalized = isNaN(Number(value)) ? value : Number(value); await api('/admin/settings/'+key,{method:'POST', body:JSON.stringify({value: normalized})}); await loadSettings(); }
async function loadNotifications(){ const rows = await api('/admin/notifications'); document.getElementById('content').innerHTML = '<h3>Уведомления</h3>'+table(rows, ['id','case_id','event','title','status','text']); }
function loadExports(){ const token = encodeURIComponent(document.getElementById('token').value || 'dev-admin-token'); document.getElementById('content').innerHTML = `<h3>Экспорт CSV</h3><p class="muted">Скачивание данных для контроля, сверки и резервной операционной выгрузки.</p><div class="actions"><a href="/admin/export/cases.csv?token=${token}" target="_blank"><button>Дела</button></a><a href="/admin/export/clients.csv?token=${token}" target="_blank"><button>Клиенты</button></a><a href="/admin/export/payments.csv?token=${token}" target="_blank"><button>Оплаты</button></a><a href="/admin/export/documents.csv?token=${token}" target="_blank"><button>Документы</button></a></div><p class="muted">В проде лучше выключить ALLOW_TOKEN_QUERY и пользоваться API с заголовком x-admin-token.</p>`; }
async function loadReady(){ const r = await fetch('/ready').then(x=>x.json()); document.getElementById('content').innerHTML = '<h3>Готовность сервиса</h3><pre>'+esc(JSON.stringify(r,null,2))+'</pre>'; document.getElementById('raw').textContent = JSON.stringify(r,null,2); }
async function runScheduler(){ await api('/admin/scheduler/run-once',{method:'POST'}); document.getElementById('content').innerHTML = '<h3>Проверки выполнены</h3><p>Scheduler run-once завершен.</p>'; }
loadSession()
  .then(() => loadDashboard())
  .catch(e => document.getElementById('content').innerHTML = '<b>Ошибка:</b> '+esc(e.message));
</script>
</body>
</html>
"""
