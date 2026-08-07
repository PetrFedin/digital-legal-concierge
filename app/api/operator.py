from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import settings

router = APIRouter(tags=["operator"])


def _state(enabled: bool) -> tuple[str, str]:
    return ("Работает", "ok") if enabled else ("Отключено", "warn")


def _payment_state() -> tuple[str, str, str]:
    provider = settings.payment_provider.strip().lower()
    if provider == "disabled":
        return (
            "Оплата отключена",
            "warn",
            "Клиентские и рабочие сценарии продолжаются без создания платёжных ссылок.",
        )
    if provider == "yookassa":
        return (
            "ЮKassa подключена",
            "ok",
            "Онлайн-платежи принимаются через ЮKassa.",
        )
    return (
        f"Провайдер: {provider or 'не задан'}",
        "warn",
        "Проверьте платёжные настройки до приёма реальных оплат.",
    )


@router.get("/operator", response_class=HTMLResponse)
async def operator_page():
    bot_label, bot_class = _state(settings.run_bot)
    scheduler_label, scheduler_class = _state(settings.run_scheduler)
    payment_label, payment_class, payment_note = _payment_state()

    html = """
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Digital Legal Concierge — рабочее пространство</title>
<style>
:root{
  --bg:#f4f6fa;--surface:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;
  --primary:#3157d5;--primary-soft:#eef2ff;--green:#14804a;--green-soft:#ecfdf3;
  --amber:#a15c00;--amber-soft:#fff7e6;--red:#b42318;--red-soft:#fef3f2;
  --shadow:0 12px 34px rgba(16,24,40,.07)
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}
header{background:linear-gradient(135deg,#111827,#25304a);color:#fff;padding:24px}
.header-inner{max-width:1180px;margin:auto;display:flex;justify-content:space-between;gap:18px;align-items:center}
h1{margin:0 0 6px;font-size:clamp(24px,4vw,34px)}header p{margin:0;color:#d0d5dd}.env{font-size:12px;color:#d0d5dd}
.header-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.header-actions button{background:#475467}
main{max-width:1180px;margin:auto;padding:22px}.intro{display:flex;justify-content:space-between;gap:16px;align-items:end;margin-bottom:14px}.intro h2{margin:0 0 4px}.muted{color:var(--muted);font-size:13px}
.status-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:20px}.status-card{background:var(--surface);border:1px solid var(--line);border-radius:15px;padding:15px;box-shadow:var(--shadow)}.status-top{display:flex;justify-content:space-between;gap:10px;align-items:center}.status-card h3{font-size:15px;margin:0}.status-card p{margin:8px 0 0;color:var(--muted);font-size:13px;line-height:1.45}
.pill{display:inline-flex;border-radius:999px;padding:5px 9px;font-size:12px;font-weight:750}.pill.ok{color:var(--green);background:var(--green-soft)}.pill.warn{color:var(--amber);background:var(--amber-soft)}
.workspace-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.workspace{background:var(--surface);border:1px solid var(--line);border-radius:17px;padding:17px;box-shadow:var(--shadow)}.workspace h3{margin:0 0 5px}.workspace>p{margin:0 0 13px;color:var(--muted);font-size:13px;line-height:1.45}
.links{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.link{display:block;text-decoration:none;color:var(--ink);border:1px solid var(--line);border-radius:12px;padding:12px;background:#fff;transition:.15s ease}.link:hover,.link:focus{border-color:#b9c4f7;background:var(--primary-soft);outline:none}.link b{display:block;font-size:14px;margin-bottom:3px}.link span{display:block;color:var(--muted);font-size:12px;line-height:1.35}.link.primary{background:var(--primary);border-color:var(--primary);color:#fff}.link.primary span{color:#dbe4ff}.link.green{background:var(--green-soft);border-color:#abefc6}.link.warn{background:var(--amber-soft);border-color:#fedf89}
.notice{margin:0 0 15px;border:1px solid #c7d2fe;background:var(--primary-soft);border-radius:13px;padding:12px;font-size:13px}.notice.error{border-color:#fecdca;background:var(--red-soft);color:var(--red)}
.role-badge{display:inline-flex;background:#eef2f6;color:#344054;border-radius:999px;padding:6px 9px;font-size:12px;font-weight:750}.section-title{margin:20px 0 10px}.section-title h2{margin:0 0 3px;font-size:19px}.loading,.empty{grid-column:1/-1;background:#fff;border:1px dashed var(--line);border-radius:15px;padding:28px;text-align:center;color:var(--muted)}
button{border:0;border-radius:10px;padding:9px 12px;color:#fff;background:var(--primary);font-weight:750;cursor:pointer}
@media(max-width:900px){.workspace-grid{grid-template-columns:1fr}.status-grid{grid-template-columns:1fr 1fr}}
@media(max-width:620px){header,.header-inner,.intro{align-items:flex-start}.header-inner,.intro{flex-direction:column}.status-grid,.links{grid-template-columns:1fr}main{padding:14px}.header-actions{width:100%}.header-actions form,.header-actions button{width:100%}}
</style>
</head>
<body>
<header>
  <div class="header-inner">
    <div>
      <h1>⚖ Digital Legal Concierge</h1>
      <p>Рабочие разделы показываются в соответствии с вашей ролью.</p>
      <div class="env">Среда: __ENV__</div>
    </div>
    <div class="header-actions">
      <span id="roleName" class="role-badge">Проверка роли…</span>
      <form method="post" action="/logout" style="margin:0"><button type="submit">Выйти</button></form>
    </div>
  </div>
</header>
<main>
  <div class="intro">
    <div><h2>Рабочее пространство</h2><div class="muted">Сначала — ежедневные действия. Технические разделы вынесены отдельно.</div></div>
  </div>
  <div id="accessNotice" class="notice">Проверяем доступные разделы…</div>
  <div class="status-grid">
    <article class="status-card"><div class="status-top"><h3>Telegram-бот</h3><span class="pill __BOT_CLASS__">__BOT_LABEL__</span></div><p>Принимает обращения и ведёт клиента по расчёту, делу и консультации.</p></article>
    <article class="status-card"><div class="status-top"><h3>Автоматические проверки</h3><span class="pill __SCHED_CLASS__">__SCHED_LABEL__</span></div><p>Контролирует сроки, уведомления, консультации и системные задания.</p></article>
    <article class="status-card"><div class="status-top"><h3>Платежи</h3><span class="pill __PAY_CLASS__">__PAY_LABEL__</span></div><p>__PAY_NOTE__</p></article>
  </div>

  <section>
    <div class="section-title"><h2>Ежедневная работа</h2><div class="muted">Только разделы, доступные текущей роли</div></div>
    <div id="workspaces" class="workspace-grid"><div class="loading">Загрузка доступных кабинетов…</div></div>
  </section>

  <section id="systemSection" hidden>
    <div class="section-title"><h2>Контроль и настройка</h2><div class="muted">Административные и технические разделы</div></div>
    <div id="systemLinks" class="workspace-grid"></div>
  </section>

  <noscript>
    <section>
      <div class="section-title"><h2>Рабочие разделы без JavaScript</h2><div class="muted">Откройте только раздел, разрешённый вашей ролью.</div></div>
      <article class="workspace">
        <div class="links">
          <a class="link" href="/admin/workdesk/ui"><b>Единый рабочий стол</b><span>Операционная очередь администратора</span></a>
          <a class="link" href="/lawyer/workspace/ui"><b>Рабочий кабинет юриста</b><span>Дела, документы и сроки</span></a>
          <a class="link" href="/lawyer/consultation-desk/ui"><b>Подготовка консультаций</b><span>Вопрос, материалы, встреча и результат</span></a>
          <a class="link" href="/document-access/review/ui"><b>Проверка документов</b><span>Решение по документам</span></a>
          <a class="link" href="/message-center/ui"><b>Сообщения</b><span>Переписка по делам</span></a>
          <a class="link" href="/admin/notification-delivery/ui"><b>Telegram-доставка</b><span>Контроль очереди и повторов</span></a>
        </div>
      </article>
    </section>
  </noscript>
</main>
<script>
const workspaces=document.getElementById('workspaces'),notice=document.getElementById('accessNotice'),roleName=document.getElementById('roleName'),systemSection=document.getElementById('systemSection'),systemLinks=document.getElementById('systemLinks');
function esc(v){return String(v??'').replace(/[&<>\x22\x27]/g,c=>c==='&'?'&amp;':c==='<'?'&lt;':c==='>'?'&gt;':c.charCodeAt(0)===34?'&quot;':'&#39;')}
function link(href,title,description,cls=''){return `<a class="link ${cls}" href="${href}"><b>${esc(title)}</b><span>${esc(description)}</span></a>`}
function group(title,description,links){return `<article class="workspace"><h3>${esc(title)}</h3><p>${esc(description)}</p><div class="links">${links.join('')}</div></article>`}
function roleLabel(roles){const labels=[];if(roles.includes('lawyer'))labels.push('Юрист');if(roles.includes('admin'))labels.push('Администратор');if(roles.includes('superadmin'))labels.push('Суперадминистратор');return labels.join(' · ')||'Сотрудник'}
function renderRoles(roles){
  const isLawyer=roles.includes('lawyer'),isAdmin=roles.includes('admin')||roles.includes('superadmin');
  roleName.textContent=roleLabel(roles);
  const groups=[];
  if(isLawyer){groups.push(group('Работа юриста','Мои дела, документы, сроки и консультации без перехода в административные очереди',[
    link('/lawyer/workspace/ui','Мои дела','Приоритеты, ближайшее действие, документы и SLA','primary'),
    link('/lawyer/consultation-desk/ui','Консультации','Подготовка, встреча, результат и неявки','green'),
    link('/document-access/review/ui','Проверка документов','Скачать файл и зафиксировать решение'),
    link('/message-center/ui','Переписка','Диалоги клиентов по конкретным делам')
  ]))}
  if(isAdmin){groups.push(group('Операционная работа','Очередь обращений и действия администратора по конкретным делам',[
    link('/admin/workdesk/ui','Единый рабочий стол','Что требует внимания сейчас и какое действие выполнить','primary'),
    link('/message-center/ui','Сообщения','Непрочитанные обращения и ответы команды'),
    link('/document-access/review/ui','Документы','Проверка и контроль версий'),
    link('/admin/consultation-outcomes/ui','Исходы консультаций','Результаты, переносы и неявки')
  ]))}
  workspaces.innerHTML=groups.join('')||'<div class="empty">Для текущей роли рабочий кабинет не настроен. Обратитесь к администратору доступа.</div>';
  if(isAdmin){
    systemSection.hidden=false;
    systemLinks.innerHTML=group('Контроль сервиса','Сроки, безопасность и эксплуатация',[
      link('/admin/sla/ui','SLA и просрочки','Сроки реакции, действия и эскалации','warn'),
      link('/admin/notification-delivery/ui','Telegram-доставка','Ошибки, очередь и повторы'),
      link('/monitoring-center/ui','Мониторинг','Работоспособность приложения'),
      link('/security-events/ui','Безопасность','События и подозрительные действия')
    ])+group('Настройка','Редкие административные операции вынесены из ежедневной очереди',[
      link('/admin-ui','Расширенная админ-панель','Справочники и корректирующие операции'),
      link('/access/ui','Пользователи и права','Роли, доступ и MFA'),
      link('/settings-ui','Настройки','Рабочие параметры приложения'),
      link('/audit-center/ui','Аудит','Журнал и целостность действий')
    ]);
  }else{systemSection.hidden=true;systemLinks.innerHTML=''}
  if(isLawyer&&isAdmin)notice.textContent='У вас совмещённые права: доступны кабинет юриста и административная очередь.';
  else if(isLawyer)notice.textContent='Открыт контур юриста. Административные разделы скрыты, чтобы не создавать лишних и недоступных переходов.';
  else if(isAdmin)notice.textContent='Открыт административный контур. Для работы юриста нужна отдельная роль lawyer.';
  else{notice.textContent='Для этой учётной записи нет рабочего контура.';notice.classList.add('error')}
}
async function boot(){
  try{
    const response=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});
    if(!response.ok){location.href='/login';return}
    const session=await response.json();
    const roles=[...new Set((session.roles||[session.role]).filter(Boolean).map(String))];
    renderRoles(roles);
  }catch(error){
    notice.textContent='Не удалось определить роль: '+(error.message||error);
    notice.classList.add('error');
    workspaces.innerHTML='<div class="empty">Обновите страницу. Если ошибка повторяется, откройте страницу входа и авторизуйтесь заново.</div>';
  }
}
boot();
</script>
</body>
</html>
"""
    replacements = {
        "__ENV__": escape(settings.app_env or "unknown"),
        "__BOT_CLASS__": bot_class,
        "__BOT_LABEL__": escape(bot_label),
        "__SCHED_CLASS__": scheduler_class,
        "__SCHED_LABEL__": escape(scheduler_label),
        "__PAY_CLASS__": payment_class,
        "__PAY_LABEL__": escape(payment_label),
        "__PAY_NOTE__": escape(payment_note),
    }
    for marker, value in replacements.items():
        html = html.replace(marker, value)
    return HTMLResponse(html)


@router.get("/operator/status")
async def operator_status():
    return {
        "version": "1.0.0-v46",
        "bot_enabled": settings.run_bot,
        "scheduler_enabled": settings.run_scheduler,
        "payment_provider": settings.payment_provider,
        "storage_dir": settings.storage_dir,
        "public_base_url": settings.public_base_url,
        "recommended_next_step": "Откройте /operator: рабочие разделы будут показаны по вашей роли",
        "workspaces": {
            "admin": "/admin/workdesk/ui",
            "lawyer": "/lawyer/workspace/ui",
            "lawyer_consultations": "/lawyer/consultation-desk/ui",
            "document_review": "/document-access/review/ui",
            "messages": "/message-center/ui",
            "sla": "/admin/sla/ui",
            "consultation_outcomes": "/admin/consultation-outcomes/ui",
        },
    }


# Mounted here because /operator is the role-workspace hub. Keep the complete
# staff router composition together: the UI links and their supporting exact-case
# endpoints must enter the application route table as one E2E surface.
from app.api.case_timeline import router as case_timeline_router  # noqa: E402
from app.api.lawyer_consultation_desk import (  # noqa: E402
    router as lawyer_consultation_desk_router,
)
from app.api.lawyer_workspace import router as lawyer_workspace_router  # noqa: E402
from app.api.notification_delivery import (  # noqa: E402
    router as notification_delivery_router,
)
from app.api.workdesk import router as workdesk_router  # noqa: E402

router.include_router(case_timeline_router)
router.include_router(lawyer_consultation_desk_router)
router.include_router(lawyer_workspace_router)
router.include_router(notification_delivery_router)
router.include_router(workdesk_router)
