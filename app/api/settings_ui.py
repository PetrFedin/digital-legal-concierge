from fastapi import APIRouter, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.system.settings_defaults import DEFAULT_SETTINGS
from app.system.settings_service import SettingsService

router = APIRouter(tags=["settings-ui"])


@router.get("/settings-ui", response_class=HTMLResponse)
async def settings_ui(db: AsyncSession = Depends(get_db)):
    service = SettingsService(db)
    rows = []
    for key, meta in DEFAULT_SETTINGS.items():
        try:
            value = await service.get_value(key)
        except Exception:
            value = meta.get("value")
        rows.append((key, meta.get("title", key), meta.get("type", "string"), value, meta.get("editable", True)))
    inputs = "".join(
        f"""
        <tr><td><code>{key}</code></td><td>{title}</td><td>{typ}</td><td>
          <form method='post' action='/settings-ui/update' style='display:flex;gap:8px'>
            <input type='hidden' name='key' value='{key}'>
            <input name='value' value='{value}' {'disabled' if not editable else ''}>
            <button type='submit' {'disabled' if not editable else ''}>OK</button>
          </form>
        </td></tr>
        """ for key, title, typ, value, editable in rows
    )
    html = f"""
    <!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
    <title>Settings UI v21</title><style>
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:#f6f7fb;color:#111827}} header{{background:#111827;color:white;padding:24px}}
    main{{max-width:1100px;margin:auto;padding:22px}} .card{{background:white;border:1px solid #e5e7eb;border-radius:16px;padding:16px}} table{{width:100%;border-collapse:collapse}} td,th{{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left}}
    input{{width:100%;padding:9px;border:1px solid #d1d5db;border-radius:8px}} button,a.button{{display:inline-block;background:#2563eb;color:white;text-decoration:none;padding:10px 14px;border:0;border-radius:10px;font-weight:700;margin:8px 4px 0 0}}
    code{{background:#f3f4f6;padding:2px 6px;border-radius:6px}}
    </style></head><body><header><h1>⚙ Настройки v21</h1><p>Суммы, проценты и сроки без редактирования кода.</p></header><main><section class='card'>
    <table><thead><tr><th>Ключ</th><th>Название</th><th>Тип</th><th>Значение</th></tr></thead><tbody>{inputs}</tbody></table><a class='button' href='/operator'>Назад</a>
    <p>После изменения настроек перезапуск обычно не нужен. Для боевых платежей проверьте платежного провайдера отдельно.</p></section></main></body></html>
    """
    return HTMLResponse(html)



@router.post("/settings-ui/update")
async def settings_update(key: str = Form(...), value: str = Form(...), db: AsyncSession = Depends(get_db)):
    await SettingsService(db).set_value(key=key, value=value, actor_id=0)
    await db.commit()
    return RedirectResponse(url="/settings-ui", status_code=303)
