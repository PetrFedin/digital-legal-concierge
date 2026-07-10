from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from app.config import settings

router = APIRouter(tags=["Initial Setup Wizard v25"])

@router.get("/initial-setup-wizard/ui", response_class=HTMLResponse)
async def ui():
    html = """
    <!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
    <title>Initial Setup Wizard v25</title><style>
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
    <header><h1>Initial Setup Wizard v25</h1><p>Production v25: эксплуатационный слой Telegram-бота.</p></header>
    <main>
      <section class='grid'><div class='card'><span class='badge'>1</span><h3>Компания</h3><p>Название, контакты, реквизиты, публичные данные.</p></div><div class='card'><span class='badge'>2</span><h3>Тарифы</h3><p>Первый платеж, второй платеж, консультация, Success Fee.</p></div><div class='card'><span class='badge'>3</span><h3>Рабочее время</h3><p>График, праздники, слоты консультаций.</p></div><div class='card'><span class='badge'>4</span><h3>Юристы</h3><p>Список юристов, лимиты нагрузки, специализации.</p></div><div class='card'><span class='badge'>5</span><h3>Проверка</h3><p>Проверка Telegram, БД, платежей и хранилища.</p></div><div class='card'><span class='badge'>6</span><h3>Готово</h3><p>Фиксация первого запуска и переход к работе.</p></div></section>
      <section class='card'><h2>Быстрые действия</h2>
        <a class='button' href='/initial-setup-wizard/status'>JSON статус</a>
        <a class='button' href='/operator'>Операторская</a>
        <a class='button' href='/launch-check'>Launch check</a>
        <a class='button' href='/ready'>Ready</a>
      </section>
      
    </main></body></html>
    """
    return HTMLResponse(html)

@router.get("/initial-setup-wizard/status")
async def status():
    return {
        'ok': True,
        'company_profile': 'ready_for_configuration',
        'tariffs_editable': True,
        'working_hours_editable': True,
        'lawyers_setup': True,
        'next_step': '/settings-ui',
    }

