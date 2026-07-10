from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from app.config import settings

router = APIRouter(tags=["Operations Center v25"])

@router.get("/operations-center/ui", response_class=HTMLResponse)
async def ui():
    html = """
    <!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
    <title>Operations Center v25</title><style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; margin:0; background:#f6f7fb; color:#111827; }
header { background:#111827; color:white; padding:24px; }
main { padding:22px; max-width:1200px; margin:auto; display:grid; gap:16px; }
.grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }
.card { background:white; border:1px solid #e5e7eb; border-radius:16px; padding:16px; box-shadow:0 1px 2px rgba(0,0,0,.04); }
.badge { display:inline-block; padding:4px 8px; border-radius:999px; background:#eef2ff; color:#3730a3; font-weight:700; font-size:12px; }
a.button { display:inline-block; padding:10px 14px; border-radius:10px; background:#2563eb; color:white; text-decoration:none; font-weight:700; margin:4px 4px 4px 0; }
pre, code { background:#0b1020; color:#d1e7ff; border-radius:10px; padding:10px; display:block; overflow:auto; }
.ok { color:#16a34a; font-weight:700; } .warn { color:#ca8a04; font-weight:700; } .bad { color:#dc2626; font-weight:700; }
table { width:100%; border-collapse:collapse; } td, th { border-bottom:1px solid #e5e7eb; padding:8px; text-align:left; }
@media(max-width:800px){ .grid { grid-template-columns:1fr; } }
</style></head><body>
    <header><h1>Operations Center v25</h1><p>Production v25: эксплуатационный слой Telegram-бота.</p></header>
    <main>
      <section class='grid'><div class='card'><span class='badge'>Cases</span><h3>Очередь обращений</h3><p>Новые дела, активные дела, просрочки.</p></div><div class='card'><span class='badge'>Documents</span><h3>Очередь документов</h3><p>Загружено, на проверке, нужно перезагрузить.</p></div><div class='card'><span class='badge'>Payments</span><h3>Очередь оплат</h3><p>Ожидают оплаты, подтверждены, ошибки.</p></div><div class='card'><span class='badge'>Consultations</span><h3>Консультации</h3><p>Слоты, записи, переносы.</p></div><div class='card'><span class='badge'>Messages</span><h3>Сообщения</h3><p>Новые вопросы клиентов.</p></div><div class='card'><span class='badge'>Deadlines</span><h3>Сроки</h3><p>30 дней после претензии, судебные события.</p></div></section>
      <section class='card'><h2>Быстрые действия</h2>
        <a class='button' href='/operations-center/status'>JSON статус</a>
        <a class='button' href='/operator'>Операторская</a>
        <a class='button' href='/launch-check'>Launch check</a>
        <a class='button' href='/ready'>Ready</a>
      </section>
      
    </main></body></html>
    """
    return HTMLResponse(html)

@router.get("/operations-center/status")
async def status():
    return {
        'ok': True,
        'queues': ['cases','documents','payments','consultations','messages','deadlines'],
        'links': {
            'tasks': '/task-center/ui',
            'messages': '/message-center/ui',
            'search': '/search-center/ui',
            'admin': '/admin-ui',
        },
    }

