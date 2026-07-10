from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.security.access_control import create_access_token, decode_access_token, verify_password

router = APIRouter(tags=["auth"])


def extract_admin_token(request: Request, header_token: str | None = None, query_token: str | None = None) -> str | None:
    if header_token:
        return header_token
    cookie_token = request.cookies.get(settings.admin_session_cookie)
    if cookie_token:
        return cookie_token
    if settings.allow_token_query and query_token:
        return query_token
    return None


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(LOGIN_HTML)


@router.post("/login")
async def login(username: str = Form(...), password: str = Form(...), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AdminUser).where(or_(AdminUser.username == username.strip(), AdminUser.email == username.strip())))
    user = result.scalars().first()
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    token = create_access_token(user.id, user.username or user.email, user.role)
    response = RedirectResponse(url="/admin-ui", status_code=303)
    response.set_cookie(key=settings.admin_session_cookie, value=token, httponly=True, samesite="lax", secure=settings.app_env == "production", max_age=60 * 60 * 12)
    return response


@router.get("/auth/session")
async def auth_session(request: Request):
    token = request.cookies.get(settings.admin_session_cookie)
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Требуется вход")
    return {"authenticated": True, "username": payload.get("username"), "role": payload.get("role"), "roles": payload.get("roles", [payload.get("role")]), "api_token": token}


@router.post("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(settings.admin_session_cookie)
    return response


LOGIN_HTML = """
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Вход в Digital Legal Concierge</title><style>
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f3f4f6;color:#111827;display:grid;place-items:center;min-height:100vh}.card{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:24px;width:min(440px,92vw);box-shadow:0 8px 30px rgba(0,0,0,.08)}input{width:100%;box-sizing:border-box;margin:8px 0 14px;padding:12px;border:1px solid #d1d5db;border-radius:12px}button{width:100%;border:0;border-radius:12px;background:#111827;color:#fff;padding:12px;font-weight:800}.muted{color:#6b7280;font-size:13px}</style></head>
<body><form class="card" method="post" action="/login"><h1>⚖ Вход в админку</h1><p class="muted">Введите логин или email и пароль. Один пользователь может одновременно быть юристом и администратором. Права назначает суперадминистратор.</p><label>Логин или email</label><input name="username" value="admin" autocomplete="username" required><label>Пароль</label><input name="password" type="password" autocomplete="current-password" required><button>Войти</button></form></body></html>
"""
