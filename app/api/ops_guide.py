from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["ops-guide"])

@router.get("/ops-guide", response_class=HTMLResponse)
async def ops_guide():
    return HTMLResponse("""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ops Guide v19</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:#f6f7fb;color:#111827}header{background:#111827;color:#fff;padding:22px}main{padding:22px;max-width:1100px;margin:auto;display:grid;gap:16px}.card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:16px}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}code,pre{background:#0b1020;color:#d1e7ff;border-radius:10px;padding:10px;display:block;overflow:auto}a.button{display:inline-block;padding:10px 14px;background:#2563eb;color:white;border-radius:10px;text-decoration:none;font-weight:700;margin:4px}@media(max-width:800px){.grid{grid-template-columns:1fr}}</style></head><body>
<header><h1>Эксплуатация Telegram-бота v19</h1><p>Короткая инструкция: как запустить, проверить и работать каждый день.</p></header><main>
<section class="grid"><div class="card"><h3>1. Запуск</h3><pre>./run.sh</pre><p>Скрипт сам проверит окружение, базу и готовность.</p></div><div class="card"><h3>2. Управление</h3><pre>./bot-control.sh</pre><p>Меню без запоминания команд.</p></div><div class="card"><h3>3. Проверка</h3><pre>python scripts/full_check_v19.py</pre><p>Полная проверка перед демонстрацией или запуском.</p></div></section>
<section class="card"><h2>Ссылки</h2><a class="button" href="/operator">Оператор</a><a class="button" href="/admin-ui">Админка</a><a class="button" href="/scenario-map-ui">Карта сценариев</a><a class="button" href="/ready">Ready</a><a class="button" href="/launch-check">Launch check</a></section>
<section class="card"><h2>Ежедневный порядок работы</h2><ol><li>Открыть <b>/operator</b> и проверить статус.</li><li>Открыть <b>/admin-ui</b>: очередь, новые дела, оплаты, документы.</li><li>Назначить юриста или включить автоназначение.</li><li>Проверить платежи и документы.</li><li>В конце дня сделать экспорт CSV и backup.</li></ol></section>
<section class="card"><h2>Боевой минимум</h2><ul><li>BOT_TOKEN задан.</li><li>ADMIN_API_TOKEN изменен с dev-значения.</li><li>PAYMENT_WEBHOOK_SECRET изменен.</li><li>Платежный провайдер выбран: fake для теста, yookassa для реальной оплаты.</li><li>Папка storage существует и попадает в backup.</li></ul></section>
</main></body></html>
""")
