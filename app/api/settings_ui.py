"""Legacy settings page, with the same auth and validation as /admin/settings."""
from html import escape

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import actor_id_from_token
from app.api.auth import extract_admin_token
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN, decode_access_token, has_role, normalize_roles
from app.security.token_revocation import is_token_revoked
from app.system.settings_defaults import DEFAULT_SETTINGS
from app.system.settings_service import SettingsService

router = APIRouter(tags=["settings-ui"])

async def require_current_settings_admin(
    request: Request,
    db: AsyncSession,
    header_token: str | None = None,
) -> dict:
    """Require an active, unrevoked, personal administrator session.

    A correctly signed token is insufficient after logout, role demotion,
    account deactivation or session-version rotation.
    """
    token = extract_admin_token(request, header_token)
    payload = decode_access_token(token)
    if (
        not payload
        or payload.get("legacy")
        or not str(payload.get("jti") or "")
        or not has_role(payload.get("roles"), ROLE_ADMIN)
    ):
        raise HTTPException(status_code=403, detail="Требуется персональный вход администратора")
    try:
        uid = int(payload.get("uid") or 0)
        session_version = int(payload.get("sv") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=403, detail="Недействительная сессия") from None
    if uid <= 0 or session_version <= 0:
        raise HTTPException(status_code=403, detail="Недействительная сессия")
    if await is_token_revoked(db, token):
        raise HTTPException(status_code=401, detail="Сессия отозвана. Войдите повторно")

    account = await db.get(AdminUser, uid)
    token_roles = set(normalize_roles(payload.get("roles")))
    live_roles = set(normalize_roles(account.role)) if account else set()
    if (
        account is None
        or not account.is_active
        or ROLE_ADMIN not in live_roles
        or live_roles != token_roles
        or int(account.session_version or 1) != session_version
    ):
        raise HTTPException(status_code=403, detail="Права администратора изменились")
    if ROLE_SUPERADMIN in token_roles and (
        payload.get("mfa") is not True or not account.mfa_enabled
    ):
        raise HTTPException(status_code=403, detail="Требуется действующее MFA")
    return payload


@router.get("/settings-ui", response_class=HTMLResponse)
async def settings_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await require_current_settings_admin(request, db, x_admin_token)
    service = SettingsService(db)
    rows = []
    for key, meta in DEFAULT_SETTINGS.items():
        value = await service.get_value(key)
        rows.append((key, meta.get("title", key), meta.get("type", "string"), value, meta.get("editable", False)))
    inputs = "".join(
        f"""
        <tr><td><code>{escape(key)}</code></td><td>{escape(str(title))}</td>
        <td>{escape(str(typ))}</td><td>
          <form method='post' action='/settings-ui/update'>
            <input type='hidden' name='key' value='{escape(key, quote=True)}'>
            <input name='value' value='{escape(str(value), quote=True)}' {'disabled' if not editable else ''}>
            <button type='submit' {'disabled' if not editable else ''}>OK</button>
          </form></td></tr>
        """ for key, title, typ, value, editable in rows
    )
    html = f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'>
    <meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>Настройки · Digital Legal Concierge</title>
    <style>body{{font-family:Arial,sans-serif;background:#f6f7fb;color:#111827;margin:0}}
    header{{background:#111827;color:#fff;padding:24px}}
    main{{max-width:1050px;margin:auto;padding:22px}}
    .card{{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:20px}}
    table{{width:100%;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left}}
    input{{padding:10px;max-width:320px;width:100%;border:1px solid #d1d5db;border-radius:7px}}
    button{{padding:9px 12px;border:0;border-radius:7px;background:#2563eb;color:#fff}}
    </style></head><body><header><h1>Настройки</h1>
    <p>Изменения доступны только администратору и проходят проверку значений.</p></header>
    <main><section class='card'><table><thead><tr><th>Ключ</th><th>Название</th>
    <th>Тип</th><th>Значение</th></tr></thead><tbody>{inputs}</tbody></table>
    <p>Вернуться в <a href='/admin-ui'>административный кабинет</a>.</p></section></main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@router.post("/settings-ui/update")
async def settings_update(
    request: Request,
    key: str = Form(...),
    value: str = Form(...),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await require_current_settings_admin(request, db, x_admin_token)
    try:
        await SettingsService(db).set_value(
            key=key, value=value,
            actor_id=actor_id_from_token(actor),
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        raise
    return RedirectResponse(url="/settings-ui", status_code=303)
