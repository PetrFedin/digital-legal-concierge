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
  <title>Digital Legal Concierge — рабочий кабинет</title>
  <style>
    :root {
      --bg:#f3f5f9; --card:#fff; --text:#172033; --muted:#667085; --line:#e3e8ef;
      --navy:#14213d; --blue:#2856c6; --blue-soft:#eef3ff; --green:#16845b;
      --green-soft:#eaf8f1; --red:#c83d4b; --red-soft:#fff0f1; --yellow:#a76808;
      --yellow-soft:#fff7e5; --purple:#6941c6; --purple-soft:#f4f0ff; --shadow:0 4px 18px rgba(26,38,64,.06);
    }
    * { box-sizing:border-box; }
    body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif; background:var(--bg); color:var(--text); }
    button,input,select,textarea { font:inherit; }
    button { border:0; border-radius:10px; padding:9px 12px; background:var(--blue); color:white; cursor:pointer; font-weight:700; }
    button:hover { filter:brightness(.97); }
    button.secondary { background:#edf0f5; color:var(--text); }
    button.green { background:var(--green); }
    button.red { background:var(--red); }
    button.yellow { background:var(--yellow); }
    button.ghost { background:transparent; color:var(--text); border:1px solid var(--line); }
    button:disabled { opacity:.45; cursor:not-allowed; }
    a { color:inherit; }
    header {
      min-height:70px; padding:12px 22px; background:var(--navy); color:white; display:flex;
      align-items:center; justify-content:space-between; gap:18px; position:sticky; top:0; z-index:20;
      box-shadow:0 4px 18px rgba(0,0,0,.12);
    }
    .brand { display:flex; align-items:center; gap:12px; min-width:0; }
    .brand-mark { width:42px; height:42px; display:grid; place-items:center; border-radius:13px; background:rgba(255,255,255,.12); font-size:22px; }
    .brand h1 { font-size:17px; margin:0; }
    .brand p { margin:3px 0 0; font-size:12px; color:#cbd5e1; }
    .session { display:flex; align-items:center; justify-content:flex-end; gap:10px; flex-wrap:wrap; }
    .session-user { text-align:right; }
    .session-user strong { display:block; font-size:13px; }
    .session-user span { color:#cbd5e1; font-size:12px; }
    .header-action { color:white; text-decoration:none; padding:8px 10px; border-radius:9px; background:rgba(255,255,255,.10); font-size:13px; font-weight:700; }
    main { padding:20px; display:grid; gap:15px; }
    .card { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:16px; box-shadow:var(--shadow); }
    .view-panel { display:flex; justify-content:space-between; align-items:center; gap:16px; flex-wrap:wrap; }
    .view-copy h2 { margin:0 0 4px; font-size:18px; }
    .muted { color:var(--muted); font-size:13px; }
    .view-switch { display:flex; flex-wrap:wrap; gap:7px; }
    .view-switch button { background:#eef1f6; color:#3a465c; }
    .view-switch button.active { background:var(--navy); color:white; }
    .metrics { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:10px; }
    .metric-card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px; box-shadow:var(--shadow); }
    .metric { font-size:27px; font-weight:850; margin:5px 0 2px; letter-spacing:-.04em; }
    .workspace { display:grid; grid-template-columns:285px minmax(0,1fr) 320px; gap:15px; align-items:start; }
    .navigation { position:sticky; top:90px; max-height:calc(100vh - 110px); overflow:auto; }
    .nav-title { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:10px; }
    .nav-title h3 { margin:0; font-size:15px; }
    .nav-group { margin-top:15px; }
    .nav-group:first-child { margin-top:0; }
    .nav-group-title { color:var(--muted); font-size:11px; font-weight:800; text-transform:uppercase; letter-spacing:.07em; margin:0 0 7px; }
    .nav-item { width:100%; text-align:left; background:white; color:var(--text); border:1px solid var(--line); padding:11px; margin-bottom:7px; border-radius:12px; }
    .nav-item:hover { border-color:#b8c6e6; background:#f8faff; }
    .nav-item.active { border-color:var(--blue); background:var(--blue-soft); box-shadow:0 0 0 2px rgba(40,86,198,.08); }
    .nav-item-top { display:flex; align-items:center; justify-content:space-between; gap:8px; }
    .nav-item-name { font-weight:800; font-size:13px; }
    .nav-item-desc { display:block; color:var(--muted); font-size:11px; margin-top:5px; line-height:1.35; font-weight:500; }
    .owner { display:inline-flex; align-items:center; padding:3px 7px; border-radius:999px; font-size:10px; font-weight:800; white-space:nowrap; }
    .owner.management { background:var(--purple-soft); color:var(--purple); }
    .owner.manager { background:var(--blue-soft); color:var(--blue); }
    .owner.lawyer { background:var(--green-soft); color:var(--green); }
    .owner.admin { background:var(--yellow-soft); color:var(--yellow); }
    .role-guide { margin-top:16px; padding-top:14px; border-top:1px solid var(--line); }
    .role-line { display:flex; align-items:center; justify-content:space-between; gap:8px; margin:7px 0; font-size:12px; }
    .content-card { min-height:480px; }
    .content-head { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:15px; padding-bottom:13px; border-bottom:1px solid var(--line); }
    .content-head h2 { margin:0 0 5px; font-size:20px; }
    .content-meta { display:flex; gap:6px; flex-wrap:wrap; justify-content:flex-end; }
    .tag { display:inline-flex; padding:4px 8px; border-radius:999px; background:#f1f3f7; color:#465168; font-size:11px; font-weight:750; }
    .route-tag { background:#edf4ff; color:#1f5ba9; }
    .quick-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin-top:15px; }
    .quick-card { border:1px solid var(--line); border-radius:13px; padding:13px; background:#fafbfc; }
    .quick-card h4 { margin:0 0 5px; }
    .quick-card p { margin:0 0 10px; color:var(--muted); font-size:12px; line-height:1.45; }
    .side-card { position:sticky; top:90px; }
    .side-card h3 { margin-top:0; }
    .side-block { padding:12px 0; border-bottom:1px solid var(--line); }
    .side-block:first-child { padding-top:0; }
    .side-block:last-child { border-bottom:0; padding-bottom:0; }
    .actions { display:flex; flex-wrap:wrap; gap:7px; }
    .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
    .row > * { flex:1; min-width:130px; }
    input,select,textarea { padding:9px 10px; border:1px solid var(--line); border-radius:10px; width:100%; background:white; color:var(--text); }
    textarea { min-height:80px; resize:vertical; }
    .table-wrap { width:100%; overflow:auto; border:1px solid var(--line); border-radius:12px; }
    table { width:100%; border-collapse:collapse; font-size:13px; }
    th,td { border-bottom:1px solid var(--line); text-align:left; padding:10px 9px; vertical-align:top; }
    tr:last-child td { border-bottom:0; }
    th { color:var(--muted); background:#f8f9fb; font-size:10px; text-transform:uppercase; letter-spacing:.05em; white-space:nowrap; }
    .pill { display:inline-block; padding:4px 8px; border-radius:999px; background:#eef2ff; color:#3730a3; font-size:11px; font-weight:800; }
    .pill.pending { background:var(--yellow-soft); color:var(--yellow); }
    .pill.ok { background:var(--green-soft); color:var(--green); }
    .pill.error { background:var(--red-soft); color:var(--red); }
    .notice { border-radius:12px; padding:12px; margin:10px 0; background:#f7f9fc; border:1px solid var(--line); font-size:13px; line-height:1.45; }
    .notice.warning { background:var(--yellow-soft); border-color:#f0d89d; }
    .notice.danger { background:var(--red-soft); border-color:#f1c3c8; }
    details.technical summary { cursor:pointer; font-weight:750; color:var(--muted); }
    pre { white-space:pre-wrap; background:#0b1020; color:#d1e7ff; border-radius:12px; padding:12px; max-height:300px; overflow:auto; font-size:12px; }
    .empty { padding:24px; text-align:center; color:var(--muted); }
    @media (max-width:1300px) { .metrics { grid-template-columns:repeat(3,1fr); } .workspace { grid-template-columns:250px minmax(0,1fr); } .side-card { position:static; grid-column:1 / -1; } }
    @media (max-width:900px) { .workspace { grid-template-columns:1fr; } .navigation,.side-card { position:static; max-height:none; } .quick-grid { grid-template-columns:1fr; } }
    @media (max-width:650px) { header { align-items:flex-start; } .session-user { display:none; } main { padding:12px; } .metrics { grid-template-columns:repeat(2,1fr); } .content-head { flex-direction:column; } .content-meta { justify-content:flex-start; } }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="brand-mark">⚖️</div>
      <div><h1>Digital Legal Concierge</h1><p>Рабочий кабинет юридического сервиса</p></div>
    </div>
    <div class="session">
      <div class="session-user"><strong id="sessionName">Загрузка...</strong><span id="sessionWorkspace">Проверяем доступ</span></div>
      <span id="sessionRole" class="owner admin">роль</span>
      <a class="header-action" href="/login">Сменить пользователя</a>
      <form method="post" action="/logout"><button type="submit" class="ghost" style="color:white;border-color:rgba(255,255,255,.25)">Выйти</button></form>
    </div>
    <input id="token" type="hidden" />
  </header>

  <main>
    <section class="card view-panel">
      <div class="view-copy"><h2>Как показать разделы</h2><div class="muted">Переключайте структуру без изменения прав доступа. Владелец раздела показывает основную ответственность.</div></div>
      <div class="view-switch" id="viewSwitch">
        <button data-mode="all" onclick="setViewMode('all')">Все</button>
        <button data-mode="roles" onclick="setViewMode('roles')">По ролям</button>
        <button data-mode="processes" onclick="setViewMode('processes')">По процессам</button>
        <button data-mode="m1" onclick="setViewMode('m1')">Маршрут М1</button>
        <button data-mode="m2" onclick="setViewMode('m2')">Маршрут М2</button>
        <button data-mode="mine" onclick="setViewMode('mine')">Моя роль</button>
      </div>
    </section>

    <section id="dashboard" class="metrics"></section>

    <section class="workspace">
      <aside class="card navigation">
        <div class="nav-title"><h3>Разделы кабинета</h3><span id="navCount" class="pill">0</span></div>
        <div id="navigationItems"></div>
        <div class="role-guide">
          <div class="muted"><b>Зоны ответственности</b></div>
          <div class="role-line"><span>Руководитель</span><span class="owner management">контроль</span></div>
          <div class="role-line"><span>Менеджер</span><span class="owner manager">операции</span></div>
          <div class="role-line"><span>Юрист</span><span class="owner lawyer">правовая работа</span></div>
          <div class="role-line"><span>Администратор</span><span class="owner admin">система</span></div>
        </div>
      </aside>

      <section class="card content-card"><div id="content">Загрузка рабочего пространства...</div></section>

      <aside class="card side-card"><div id="side">
        <h3>Контекст раздела</h3>
        <p class="muted">Здесь будут показаны владелец процесса, подсказки и быстрые действия.</p>
      </div></aside>
    </section>

    <details class="card technical">
      <summary>Технический ответ API</summary>
      <p class="muted">Служебные данные для диагностики. В обычной работе этот блок можно не открывать.</p>
      <pre id="raw">—</pre>
    </details>
  </main>

<script>
let sessionData = {};
let currentMode = localStorage.getItem('admin_view_mode') || 'roles';
let activeSection = localStorage.getItem('admin_active_section') || 'dashboard';

document.getElementById('token').value = localStorage.getItem('admin_token') || '';

const OWNERS = {
  management: {title:'Руководитель', className:'management'},
  manager: {title:'Менеджер / оператор', className:'manager'},
  lawyer: {title:'Юрист', className:'lawyer'},
  admin: {title:'Администратор системы', className:'admin'}
};

const ROLE_TO_OWNER = {
  superadmin:'admin', admin:'admin', lawyer:'lawyer', operator:'manager', tester:'admin'
};

const SECTIONS = [
  {id:'dashboard', icon:'📊', title:'Обзор сервиса', description:'Главные показатели, очереди и точки внимания.', owner:'management', process:'Контроль и KPI', routes:['M1','M2']},
  {id:'cases', icon:'📁', title:'Все дела', description:'Единый реестр клиентских дел и текущих статусов.', owner:'manager', process:'Работа с делами', routes:['M1','M2']},
  {id:'queue', icon:'📥', title:'Очередь без юриста', description:'Нераспределённые дела, требующие назначения.', owner:'manager', process:'Распределение нагрузки', routes:['M1','M2']},
  {id:'m1', icon:'⚖️', title:'Маршрут М1', description:'Полное ведение: документы, претензия, суд и исполнение.', owner:'lawyer', process:'Маршрут М1', routes:['M1']},
  {id:'m2', icon:'💬', title:'Маршрут М2', description:'Консультации, слоты, оплата и подтверждение юриста.', owner:'lawyer', process:'Маршрут М2', routes:['M2']},
  {id:'pending', icon:'⏳', title:'М2: ждут подтверждения', description:'Оплаченные консультации, ожидающие решения юриста.', owner:'lawyer', process:'Маршрут М2', routes:['M2']},
  {id:'documents', icon:'📄', title:'Документы', description:'Комплектность, версии и движение материалов.', owner:'lawyer', process:'Документооборот', routes:['M1','M2']},
  {id:'payments', icon:'💳', title:'Оплаты', description:'Статусы платежей, ошибки и ручная проверка.', owner:'manager', process:'Финансы', routes:['M1','M2']},
  {id:'notifications', icon:'🔔', title:'Уведомления', description:'Контроль клиентских и служебных сообщений.', owner:'manager', process:'Коммуникации', routes:['M1','M2']},
  {id:'operator', icon:'🎧', title:'Кабинет оператора', description:'Ежедневная работа с обращениями клиентов.', owner:'manager', process:'Коммуникации', routes:['M1','M2'], href:'/operator'},
  {id:'lawyers', icon:'👩‍⚖️', title:'Юристы и нагрузка', description:'Состав команды, активность и лимиты нагрузки.', owner:'admin', process:'Команда', routes:['M1','M2']},
  {id:'exports', icon:'📤', title:'Экспорт и отчётность', description:'Безопасная выгрузка операционных данных.', owner:'management', process:'Контроль и KPI', routes:['M1','M2']},
  {id:'users', icon:'🔐', title:'Пользователи и роли', description:'Доступы сотрудников и разграничение полномочий.', owner:'admin', process:'Администрирование', routes:['M1','M2'], href:'/access/ui'},
  {id:'settings', icon:'⚙️', title:'Настройки сервиса', description:'Тарифы, сроки и управляемые параметры системы.', owner:'admin', process:'Администрирование', routes:['M1','M2']},
  {id:'ready', icon:'🩺', title:'Готовность сервиса', description:'Проверка доступности приложения и зависимостей.', owner:'admin', process:'Технический контроль', routes:['M1','M2']},
  {id:'scheduler', icon:'🕒', title:'Регламентные проверки', description:'Ручной запуск фоновых контрольных процедур.', owner:'admin', process:'Технический контроль', routes:['M1','M2']}
];

const COLUMN_LABELS = {
  id:'ID', number:'Номер', case_id:'Дело', route:'Маршрут', status:'Статус', lawyer_id:'Юрист',
  next_action:'Следующий шаг', code:'Код', title:'Название', amount:'Сумма', provider:'Провайдер',
  type:'Тип', file_name:'Файл', version:'Версия', full_name:'ФИО', email:'E-mail', is_active:'Активен',
  workload_limit:'Лимит', event:'Событие', text:'Текст', processing_outcome:'Результат обработки',
  manual_review_required:'Ручная проверка'
};

function ownerBadge(owner) {
  const item = OWNERS[owner] || OWNERS.admin;
  return `<span class="owner ${item.className}">${item.title}</span>`;
}

function setRaw(data) {
  document.getElementById('raw').textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
}

function errorMessage(data, fallback='Ошибка запроса') {
  if (!data) return fallback;
  if (typeof data.detail === 'string') return data.detail;
  if (data.detail) return JSON.stringify(data.detail);
  return data.message || fallback;
}

async function loadSession() {
  const response = await fetch('/auth/session');
  if (!response.ok) { location.href='/login'; return; }
  sessionData = await response.json();
  document.getElementById('token').value = sessionData.api_token || '';
  localStorage.setItem('admin_token', sessionData.api_token || '');
  document.getElementById('sessionName').textContent = sessionData.username || 'Пользователь';
  document.getElementById('sessionWorkspace').textContent = sessionData.workspace ? `Рабочее пространство: ${sessionData.workspace}` : 'Рабочее пространство администратора';
  const owner = ROLE_TO_OWNER[sessionData.role] || 'admin';
  document.getElementById('sessionRole').className = `owner ${OWNERS[owner].className}`;
  document.getElementById('sessionRole').textContent = OWNERS[owner].title;
}

const api = async (path, opts={}) => {
  const token = document.getElementById('token').value;
  const response = await fetch(path, {
    ...opts,
    headers: {'x-admin-token':token, 'Content-Type':'application/json', ...(opts.headers || {})}
  });
  const data = await response.json().catch(() => ({}));
  setRaw(data);
  if (response.status === 401) { location.href='/login'; throw new Error('Сессия завершена'); }
  if (!response.ok) throw new Error(errorMessage(data));
  return data;
};

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, symbol => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[symbol]));
}

function formatDateTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return esc(value);
  return new Intl.DateTimeFormat('ru-RU', {dateStyle:'medium', timeStyle:'short'}).format(date);
}

function formatCell(row, key) {
  const value = row[key];
  if (key === 'route') return value ? `<span class="pill">${esc(value)}</span>` : '—';
  if (key === 'status') {
    const cls = String(value).includes('PAID') || String(value).includes('CLOSED') || value === 'PAID' ? 'ok' : String(value).includes('ERROR') || value === 'FAILED' ? 'error' : 'pending';
    return `<span class="pill ${cls}">${esc(value || '—')}</span>`;
  }
  if (key === 'manual_review_required') return value ? '<span class="pill error">Требуется</span>' : '<span class="pill ok">Нет</span>';
  if (key === 'is_active') return value ? '<span class="pill ok">Да</span>' : '<span class="pill error">Нет</span>';
  if (key === 'amount') return `${Number(value || 0).toLocaleString('ru-RU', {minimumFractionDigits:2})} ₽`;
  return esc(value ?? '—');
}

function metric(title, value, note='') {
  return `<div class="metric-card"><div class="muted">${esc(title)}</div><div class="metric">${esc(value ?? 0)}</div><div class="muted">${esc(note)}</div></div>`;
}

function table(rows, columns, actionRenderer=null) {
  if (!rows.length) return '<div class="empty">Нет данных по выбранному разделу.</div>';
  const headers = columns.map(key => `<th>${esc(COLUMN_LABELS[key] || key)}</th>`).join('');
  const actionHeader = actionRenderer ? '<th>Действия</th>' : '';
  const body = rows.map(row => {
    const cells = columns.map(key => `<td>${formatCell(row, key)}</td>`).join('');
    const actions = actionRenderer ? `<td>${actionRenderer(row)}</td>` : '';
    return `<tr>${cells}${actions}</tr>`;
  }).join('');
  return `<div class="table-wrap"><table><thead><tr>${headers}${actionHeader}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function sectionHeader(title, description, owner, process, routes=[]) {
  return `<div class="content-head"><div><h2>${esc(title)}</h2><div class="muted">${esc(description)}</div></div><div class="content-meta">${ownerBadge(owner)}<span class="tag">${esc(process)}</span>${routes.map(route => `<span class="tag route-tag">${esc(route)}</span>`).join('')}</div></div>`;
}

function sideContext(section, body='') {
  document.getElementById('side').innerHTML = `
    <div class="side-block"><h3>${esc(section.icon)} ${esc(section.title)}</h3><p class="muted">${esc(section.description)}</p></div>
    <div class="side-block"><div class="muted">Основной владелец</div><p>${ownerBadge(section.owner)}</p><div class="muted">Бизнес-процесс</div><p><b>${esc(section.process)}</b></p></div>
    ${body || '<div class="side-block"><b>Подсказка</b><p class="muted">Выберите запись в основном окне, чтобы увидеть доступные действия.</p></div>'}`;
}

function sectionsForMode(mode) {
  if (mode === 'm1') return SECTIONS.filter(section => section.routes.includes('M1'));
  if (mode === 'm2') return SECTIONS.filter(section => section.routes.includes('M2'));
  if (mode === 'mine') {
    const owner = ROLE_TO_OWNER[sessionData.role] || 'admin';
    return SECTIONS.filter(section => section.owner === owner);
  }
  return SECTIONS;
}

function groupSections(sections) {
  if (currentMode === 'roles' || currentMode === 'mine') {
    return sections.reduce((acc, section) => {
      const key = OWNERS[section.owner]?.title || 'Другие';
      (acc[key] ||= []).push(section); return acc;
    }, {});
  }
  if (currentMode === 'processes' || currentMode === 'm1' || currentMode === 'm2') {
    return sections.reduce((acc, section) => { (acc[section.process] ||= []).push(section); return acc; }, {});
  }
  return {'Все разделы':sections};
}

function renderNavigation() {
  const sections = sectionsForMode(currentMode);
  const groups = groupSections(sections);
  document.getElementById('navCount').textContent = sections.length;
  document.querySelectorAll('#viewSwitch button').forEach(button => button.classList.toggle('active', button.dataset.mode === currentMode));
  document.getElementById('navigationItems').innerHTML = Object.entries(groups).map(([group, items]) => `
    <div class="nav-group"><div class="nav-group-title">${esc(group)}</div>
      ${items.map(section => `<button class="nav-item ${section.id === activeSection ? 'active' : ''}" onclick="navigate('${section.id}')">
        <span class="nav-item-top"><span class="nav-item-name">${esc(section.icon)} ${esc(section.title)}</span>${ownerBadge(section.owner)}</span>
        <span class="nav-item-desc">${esc(section.description)}</span>
      </button>`).join('')}
    </div>`).join('');
}

function setViewMode(mode) {
  currentMode = mode;
  localStorage.setItem('admin_view_mode', mode);
  renderNavigation();
}

const LOADERS = {
  dashboard:loadDashboard, cases:loadCases, queue:loadQueue, m1:loadM1Cases, m2:loadM2Cases,
  pending:loadPendingConfirmations, documents:loadDocuments, payments:loadPayments,
  notifications:loadNotifications, lawyers:loadLawyers, exports:loadExports,
  settings:loadSettings, ready:loadReady, scheduler:loadScheduler
};

async function navigate(id) {
  const section = SECTIONS.find(item => item.id === id);
  if (!section) return;
  if (section.href) { window.location.href = section.href; return; }
  activeSection = id;
  localStorage.setItem('admin_active_section', id);
  renderNavigation();
  sideContext(section);
  document.getElementById('content').innerHTML = `${sectionHeader(section.title, section.description, section.owner, section.process, section.routes)}<div class="empty">Загрузка...</div>`;
  try { await LOADERS[id](); }
  catch (error) {
    document.getElementById('content').innerHTML = `${sectionHeader(section.title, section.description, section.owner, section.process, section.routes)}<div class="notice danger"><b>Не удалось загрузить раздел.</b><br>${esc(error.message)}</div>`;
  }
}

async function loadDashboard() {
  const section = SECTIONS.find(item => item.id === 'dashboard');
  const data = await api('/admin/dashboard');
  document.getElementById('dashboard').innerHTML =
    metric('Новые дела', data.new_cases, 'Нужна первичная обработка') +
    metric('Активные дела', data.active_cases, 'М1 и М2 в работе') +
    metric('Ожидают оплату', data.waiting_payment, 'Контроль менеджера') +
    metric('М2 ждут юриста', data.consultations_pending_confirmation, 'Оплачены, не подтверждены') +
    metric('Консультации назначены', data.consultations_booked, 'Подтверждённые записи') +
    metric('Закрытые дела', data.closed_cases, 'Завершённые маршруты');
  document.getElementById('content').innerHTML = sectionHeader(section.title, section.description, section.owner, section.process, section.routes) + `
    <div class="notice ${Number(data.consultations_pending_confirmation || 0) ? 'warning' : ''}">
      <b>Операционный фокус:</b> ${Number(data.consultations_pending_confirmation || 0) ? `консультаций М2 без подтверждения юриста — ${esc(data.consultations_pending_confirmation)}.` : 'критических ожиданий подтверждения М2 сейчас нет.'}
    </div>
    <div class="quick-grid">
      <div class="quick-card"><h4>Менеджер / оператор</h4><p>Разобрать новые обращения, очередь без юриста, оплаты и сообщения клиентам.</p><div class="actions"><button onclick="navigate('queue')">Открыть очередь</button><button class="secondary" onclick="navigate('payments')">Оплаты</button></div></div>
      <div class="quick-card"><h4>Юрист</h4><p>Проверить материалы М1 и своевременно подтвердить оплаченные консультации М2.</p><div class="actions"><button class="green" onclick="navigate('m1')">Маршрут М1</button><button class="yellow" onclick="navigate('pending')">Подтверждения М2</button></div></div>
      <div class="quick-card"><h4>Руководитель</h4><p>Контролировать нагрузку, узкие места и выгружать данные для сверки.</p><div class="actions"><button onclick="navigate('cases')">Все дела</button><button class="secondary" onclick="navigate('exports')">Экспорт</button></div></div>
      <div class="quick-card"><h4>Администратор системы</h4><p>Управлять пользователями, настройками и технической готовностью.</p><div class="actions"><button onclick="navigate('settings')">Настройки</button><button class="secondary" onclick="navigate('ready')">Готовность</button></div></div>
    </div>`;
  sideContext(section, `<div class="side-block"><b>Как читать дашборд</b><p class="muted">Показатели отражают текущие рабочие очереди. Начинайте с неоплаченных этапов, нераспределённых дел и консультаций без подтверждения.</p></div>`);
}

function caseAction(row) { return `<button onclick="openCase(${Number(row.id)})">Открыть</button>`; }

async function loadCasesByRoute(route=null) {
  const rows = await api('/admin/cases');
  const filtered = route ? rows.filter(row => row.route === route) : rows;
  const id = route === 'M1' ? 'm1' : route === 'M2' ? 'm2' : 'cases';
  const section = SECTIONS.find(item => item.id === id);
  document.getElementById('content').innerHTML = sectionHeader(section.title, `${section.description} Найдено: ${filtered.length}.`, section.owner, section.process, section.routes) + table(filtered, ['id','number','route','status','lawyer_id','next_action'], caseAction);
  sideContext(section, `<div class="side-block"><b>Распределение ответственности</b><p class="muted">Менеджер контролирует движение дела и коммуникации; юрист отвечает за правовые этапы и содержание документов; администратор не должен подменять решения юриста.</p></div>`);
}

async function loadCases() { return loadCasesByRoute(); }
async function loadM1Cases() { return loadCasesByRoute('M1'); }
async function loadM2Cases() { return loadCasesByRoute('M2'); }

async function loadQueue() {
  const section = SECTIONS.find(item => item.id === 'queue');
  const rows = await api('/admin/queue');
  document.getElementById('content').innerHTML = sectionHeader(section.title, `${section.description} В очереди: ${rows.length}.`, section.owner, section.process, section.routes) +
    (rows.length ? '<div class="notice warning">Назначение должно учитывать активность юриста и допустимую нагрузку.</div>' : '') +
    table(rows, ['id','number','route','status','next_action'], row => `<div class="actions"><button class="green" onclick="autoAssign(${Number(row.id)})">Автоназначить</button><button class="secondary" onclick="openCase(${Number(row.id)})">Открыть</button></div>`);
}

async function loadPendingConfirmations() {
  const section = SECTIONS.find(item => item.id === 'pending');
  const rows = await api('/admin/consultations/pending-confirmation');
  const body = rows.map(row => `<tr>
    <td>${esc(row.consultation_id)}</td>
    <td><button class="secondary" onclick="openCase(${Number(row.case_id)})">${esc(row.case_number)}</button><div class="muted">${esc(row.case_title || '')}</div></td>
    <td>${esc(row.lawyer_name || 'Не найден')}<div class="muted">${esc(row.lawyer_email || '')}</div></td>
    <td>${formatDateTime(row.scheduled_at)}</td><td>${esc(row.consultation_type || '—')}</td>
    <td>${row.integrity_issue ? `<span class="pill error">${esc(row.integrity_issue)}</span>` : '<span class="pill pending">Ждёт юриста</span>'}</td>
  </tr>`).join('');
  document.getElementById('content').innerHTML = sectionHeader(section.title, section.description, section.owner, section.process, section.routes) +
    (rows.length ? `<div class="notice warning"><b>${rows.length}</b> консультаций требуют внимания. Администратор контролирует срок, но подтверждает консультацию назначенный юрист.</div><div class="table-wrap"><table><thead><tr><th>Консультация</th><th>Дело</th><th>Юрист</th><th>Дата</th><th>Формат</th><th>Контроль</th></tr></thead><tbody>${body}</tbody></table></div>` : '<div class="empty">Все оплаченные консультации подтверждены.</div>');
  sideContext(section, `<div class="side-block"><b>Правило процесса</b><p class="muted">Нельзя обходить подтверждение юриста административной сменой статуса. При проблеме сначала проверьте назначение, активность юриста и целостность консультации.</p></div>`);
}

function paymentStatusClass(payment) {
  if (payment.manual_review_required || payment.processing_error) return 'error';
  return payment.status === 'PAID' ? 'ok' : 'pending';
}

function paymentAction(payment, caseId=0) {
  if (payment.status === 'PAID' && !payment.manual_review_required) return '<span class="pill ok">Подтверждено</span>';
  return `<button class="${payment.manual_review_required ? 'yellow' : 'green'}" onclick="confirmPayment(${Number(payment.id)}, ${Number(caseId)})">${payment.manual_review_required ? 'Повторно обработать' : 'Подтвердить вручную'}</button>`;
}

async function openCase(id) {
  const data = await api('/admin/cases/' + id);
  const item = data.case, client = data.client || {}, payments = data.payments || [], documents = data.documents || [];
  const routeTitle = item.route === 'M1' ? 'Полное ведение дела' : item.route === 'M2' ? 'Консультация юриста' : 'Маршрут не выбран';
  const paymentRows = payments.map(payment => `<tr><td>${payment.id}</td><td>${esc(payment.title)}</td><td>${Number(payment.amount || 0).toLocaleString('ru-RU')} ₽</td><td><span class="pill ${paymentStatusClass(payment)}">${esc(payment.status)}</span>${payment.processing_error ? `<div class="muted">${esc(payment.processing_error)}</div>` : ''}</td><td>${paymentAction(payment, id)}</td></tr>`).join('') || '<tr><td colspan="5" class="empty">Платежей нет</td></tr>';
  const documentRows = documents.map(document => `<tr><td>${document.id}</td><td>${esc(document.title)}</td><td>${esc(document.file_name)}</td><td>${esc(document.status)}</td><td>v${esc(document.version)}</td></tr>`).join('') || '<tr><td colspan="5" class="empty">Документов нет</td></tr>';
  document.getElementById('content').innerHTML = sectionHeader(`Дело ${item.number}`, routeTitle, item.route === 'M1' ? 'lawyer' : 'manager', item.route === 'M1' ? 'Маршрут М1' : 'Маршрут М2', [item.route].filter(Boolean)) + `
    <div class="notice"><b>Текущий статус:</b> ${esc(item.status)}<br><b>Следующий шаг:</b> ${esc(item.next_action || 'Не определён')}</div>
    <h3>Платежи <span class="pill">${payments.length}</span></h3><div class="table-wrap"><table><thead><tr><th>ID</th><th>Название</th><th>Сумма</th><th>Статус</th><th>Действие</th></tr></thead><tbody>${paymentRows}</tbody></table></div>
    <h3>Документы <span class="pill">${documents.length}</span></h3><div class="table-wrap"><table><thead><tr><th>ID</th><th>Название</th><th>Файл</th><th>Статус</th><th>Версия</th></tr></thead><tbody>${documentRows}</tbody></table></div>`;
  document.getElementById('side').innerHTML = `
    <div class="side-block"><h3>Дело ${esc(item.number)}</h3><p><span class="pill">${esc(item.route || '—')}</span> <span class="pill pending">${esc(item.status)}</span></p></div>
    <div class="side-block"><div class="muted">Клиент</div><p><b>${esc(client.name || 'Не указано')}</b><br>${client.username ? '@' + esc(client.username) + '<br>' : ''}Telegram ID: ${esc(client.telegram_id || '—')}</p></div>
    <div class="side-block"><div class="muted">Назначенный юрист</div><p><b>${esc(item.lawyer_id || 'Не назначен')}</b></p><div class="muted">Следующий шаг</div><p>${esc(item.next_action || 'Не определён')}</p></div>
    <div class="side-block"><h4>Доступные действия</h4><div class="actions"><button class="green" onclick="autoAssign(${id})">Автоназначить</button><button onclick="setStatus(${id})">Изменить статус</button><button class="secondary" onclick="navigate('payments')">Все оплаты</button></div></div>`;
}

async function setStatus(id) {
  const statuses = await api('/admin/statuses');
  document.getElementById('side').innerHTML += `<div class="side-block"><h4>Ручное изменение статуса</h4><div class="notice warning">Используйте только после проверки процесса. Ручной переход может обойти автоматические ограничения.</div><select id="status_${id}">${statuses.map(status => `<option value="${esc(status)}">${esc(status)}</option>`).join('')}</select><textarea id="comment_${id}" placeholder="Обязательное основание изменения"></textarea><button style="margin-top:8px" onclick="saveStatus(${id})">Сохранить статус</button></div>`;
}

async function saveStatus(id) {
  const comment = document.getElementById('comment_' + id).value.trim();
  if (!comment) { alert('Укажите основание ручного изменения статуса.'); return; }
  if (!confirm('Изменить статус дела вручную? Убедитесь, что это не обходит обязательное действие клиента или юриста.')) return;
  await api('/admin/cases/' + id + '/status', {method:'POST', body:JSON.stringify({status:document.getElementById('status_' + id).value, comment})});
  await openCase(id);
}

async function autoAssign(id) {
  const result = await api('/admin/cases/' + id + '/auto-assign', {method:'POST'});
  if (!result.ok) alert(result.message || 'Нет доступного юриста');
  await openCase(id);
}

async function confirmPayment(paymentId, caseId) {
  if (!confirm('Подтвердить платёж вручную? Это действие запускает переход по маршруту. Сначала убедитесь, что поступление действительно проверено.')) return;
  try {
    await api('/admin/payments/' + paymentId + '/confirm', {method:'POST'});
    if (caseId) await openCase(caseId); else await loadPayments();
  } catch (error) {
    alert('Платёж не был автоматически применён: ' + error.message);
    if (caseId) await openCase(caseId); else await loadPayments();
  }
}

async function loadPayments() {
  const section = SECTIONS.find(item => item.id === 'payments');
  const rows = await api('/admin/payments');
  const reviewCount = rows.filter(row => row.manual_review_required).length;
  document.getElementById('content').innerHTML = sectionHeader(section.title, section.description, section.owner, section.process, section.routes) +
    (reviewCount ? `<div class="notice danger"><b>Требуют ручной проверки: ${reviewCount}.</b> Не подтверждайте повторно без сверки фактического поступления и текущего этапа дела.</div>` : '<div class="notice">Платежей с признаком обязательной ручной проверки сейчас нет.</div>') +
    table(rows, ['id','case_id','code','amount','status','processing_outcome','manual_review_required'], row => paymentAction(row, 0));
  sideContext(section, `<div class="side-block"><b>Разделение ролей</b><p class="muted">Менеджер сверяет факт оплаты и проблему провайдера. Юрист не должен вручную подтверждать финансы. Администратор вмешивается только при техническом конфликте.</p></div>`);
}

async function loadDocuments() {
  const section = SECTIONS.find(item => item.id === 'documents');
  const rows = await api('/admin/documents');
  document.getElementById('content').innerHTML = sectionHeader(section.title, `${section.description} Документов: ${rows.length}.`, section.owner, section.process, section.routes) + table(rows, ['id','case_id','type','title','file_name','status','version']);
  sideContext(section, `<div class="side-block"><b>Роли</b><p class="muted">Менеджер контролирует получение и комплектность. Юрист оценивает содержание и юридическую достаточность материалов.</p></div>`);
}

async function loadLawyers() {
  const section = SECTIONS.find(item => item.id === 'lawyers');
  const rows = await api('/admin/lawyers');
  document.getElementById('content').innerHTML = sectionHeader(section.title, section.description, section.owner, section.process, section.routes) + table(rows, ['id','full_name','email','is_active','workload_limit']) + `
    <h3>Добавить юриста</h3><div class="notice">Создание учётной записи доступа и создание карточки юриста — разные операции. После добавления проверьте пользователя и роль.</div>
    <div class="row"><input id="lw_name" placeholder="ФИО юриста"><input id="lw_email" type="email" placeholder="E-mail"><input id="lw_limit" type="number" min="1" value="30" placeholder="Лимит нагрузки"><button onclick="createLawyer()">Создать карточку</button></div>`;
}

async function createLawyer() {
  const fullName = document.getElementById('lw_name').value.trim();
  if (!fullName) { alert('Укажите ФИО юриста.'); return; }
  await api('/admin/lawyers', {method:'POST', body:JSON.stringify({full_name:fullName, email:document.getElementById('lw_email').value.trim() || null, workload_limit:Number(document.getElementById('lw_limit').value || 30)})});
  await loadLawyers();
}

async function loadSettings() {
  const section = SECTIONS.find(item => item.id === 'settings');
  const rows = await api('/admin/settings');
  const cards = rows.map(row => `<div class="quick-card"><h4>${esc(row.title)}</h4><div class="muted">${esc(row.key)}</div><div class="row" style="margin-top:9px"><input id="set_${esc(row.key)}" value="${esc(row.value?.value ?? '')}" ${row.editable ? '' : 'disabled'}><button ${row.editable ? '' : 'disabled'} onclick="saveSetting('${esc(row.key)}')">Сохранить</button></div></div>`).join('');
  document.getElementById('content').innerHTML = sectionHeader(section.title, section.description, section.owner, section.process, section.routes) + '<div class="notice warning">Изменение параметров влияет на новые клиентские сценарии. Фиксируйте бизнес-основание и проверяйте значения после сохранения.</div><div class="quick-grid">' + cards + '</div>';
}

async function saveSetting(key) {
  const value = document.getElementById('set_' + key).value;
  const normalized = value !== '' && !Number.isNaN(Number(value)) ? Number(value) : value;
  if (!confirm(`Сохранить новое значение настройки «${key}»?`)) return;
  await api('/admin/settings/' + key, {method:'POST', body:JSON.stringify({value:normalized})});
  await loadSettings();
}

async function loadNotifications() {
  const section = SECTIONS.find(item => item.id === 'notifications');
  const rows = await api('/admin/notifications');
  document.getElementById('content').innerHTML = sectionHeader(section.title, `${section.description} Последние записи: ${rows.length}.`, section.owner, section.process, section.routes) + table(rows, ['id','case_id','event','title','status','text']);
}

function loadExports() {
  const section = SECTIONS.find(item => item.id === 'exports');
  document.getElementById('content').innerHTML = sectionHeader(section.title, section.description, section.owner, section.process, section.routes) + `
    <div class="notice"><b>Безопасная выгрузка:</b> токен доступа передаётся в заголовке запроса и не попадает в URL или историю браузера.</div>
    <div class="quick-grid">
      <div class="quick-card"><h4>Дела</h4><p>Маршруты, статусы и назначенные юристы.</p><button onclick="downloadExport('/admin/export/cases.csv','cases.csv')">Скачать CSV</button></div>
      <div class="quick-card"><h4>Клиенты</h4><p>Операционный реестр клиентов сервиса.</p><button onclick="downloadExport('/admin/export/clients.csv','clients.csv')">Скачать CSV</button></div>
      <div class="quick-card"><h4>Платежи</h4><p>Сверка статусов, сумм и провайдеров.</p><button onclick="downloadExport('/admin/export/payments.csv','payments.csv')">Скачать CSV</button></div>
      <div class="quick-card"><h4>Документы</h4><p>Перечень документов и версий по делам.</p><button onclick="downloadExport('/admin/export/documents.csv','documents.csv')">Скачать CSV</button></div>
    </div>`;
}

async function downloadExport(path, filename) {
  const response = await fetch(path, {headers:{'x-admin-token':document.getElementById('token').value}});
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(errorMessage(data, 'Не удалось скачать файл'));
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href=url; link.download=filename; document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  setRaw(`Экспорт ${filename} успешно сформирован.`);
}

async function loadReady() {
  const section = SECTIONS.find(item => item.id === 'ready');
  const response = await fetch('/ready');
  const result = await response.json().catch(() => ({}));
  setRaw(result);
  document.getElementById('content').innerHTML = sectionHeader(section.title, section.description, section.owner, section.process, section.routes) + `<div class="notice ${response.ok ? '' : 'danger'}"><b>${response.ok ? 'Сервис отвечает.' : 'Обнаружена проблема готовности.'}</b></div><pre>${esc(JSON.stringify(result, null, 2))}</pre>`;
}

async function loadScheduler() {
  const section = SECTIONS.find(item => item.id === 'scheduler');
  document.getElementById('content').innerHTML = sectionHeader(section.title, section.description, section.owner, section.process, section.routes) + `<div class="notice warning">Ручной запуск предназначен для диагностики и оперативного контроля. Не запускайте проверки многократно без необходимости.</div><button class="green" onclick="runScheduler()">Запустить один цикл проверок</button>`;
}

async function runScheduler() {
  if (!confirm('Запустить один цикл регламентных проверок сейчас?')) return;
  const result = await api('/admin/scheduler/run-once', {method:'POST'});
  document.getElementById('content').innerHTML += `<div class="notice"><b>Проверки завершены.</b><br>${esc(JSON.stringify(result))}</div>`;
}

loadSession()
  .then(() => { renderNavigation(); return navigate(activeSection); })
  .catch(error => { document.getElementById('content').innerHTML = `<div class="notice danger"><b>Ошибка загрузки кабинета.</b><br>${esc(error.message)}</div>`; });
</script>
</body>
</html>
"""
