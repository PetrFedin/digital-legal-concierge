from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.models.audit_log import AuditLog
from app.security.access_control import (
    ROLE_SUPERADMIN,
    create_access_token,
    create_mfa_challenge_token,
    decode_access_token,
    hash_password,
    normalize_roles,
    verify_password,
)
from app.security.http_security import BROWSER_SESSION_SENTINEL
from app.security.login_throttle import LoginRateLimitError, LoginThrottleService
from app.security.security_events import record_security_event
from app.security.token_revocation import is_token_revoked, revoke_token

router = APIRouter(tags=["auth"])
MFA_CHALLENGE_COOKIE = "dlc_mfa_challenge"
DUMMY_PASSWORD_HASH = hash_password("DLC-Dummy-Password-Not-For-Login-2026!")
NO_STORE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def extract_admin_token(
    request: Request,
    header_token: str | None = None,
    query_token: str | None = None,
) -> str | None:
    if header_token:
        return header_token
    cookie_token = request.cookies.get(settings.admin_session_cookie)
    if cookie_token:
        return cookie_token
    if settings.allow_token_query and settings.app_env != "production" and query_token:
        return query_token
    return None


def _client_address(request: Request) -> str:
    return request.client.host if request.client and request.client.host else "unknown"


def _set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        key=settings.admin_session_cookie,
        value=token,
        httponly=True,
        samesite="strict",
        secure=settings.app_env == "production",
        max_age=12 * 60 * 60,
        path="/",
    )


def _set_challenge_cookie(response, token: str) -> None:
    response.set_cookie(
        key=MFA_CHALLENGE_COOKIE,
        value=token,
        httponly=True,
        samesite="strict",
        secure=settings.app_env == "production",
        max_age=10 * 60,
        path="/",
    )


def _rate_limit_error(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail="Слишком много попыток входа. Повторите позже",
        headers={"Retry-After": str(max(1, int(retry_after)))},
    )


def _prefers_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "").lower()


def _login_response(
    *,
    error: str | None = None,
    username: str = "",
    status_code: int = 200,
    retry_after: int | None = None,
) -> HTMLResponse:
    error_html = ""
    if error:
        error_html = f'<div class="error" role="alert">{escape(error)}</div>'
    body = LOGIN_HTML.replace("__ERROR__", error_html).replace(
        "__USERNAME__", escape(username, quote=True)
    )
    headers = dict(NO_STORE_HEADERS)
    if retry_after is not None:
        headers["Retry-After"] = str(max(1, int(retry_after)))
    return HTMLResponse(body, status_code=status_code, headers=headers)


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return _login_response()


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    normalized_username = username.strip().lower()
    client_address = _client_address(request)
    throttle = LoginThrottleService(db)
    try:
        await throttle.check(
            principal=normalized_username,
            client_address=client_address,
        )
    except LoginRateLimitError as error:
        if _prefers_html(request):
            return _login_response(
                error="Слишком много попыток входа. Повторите позже.",
                username=username,
                status_code=429,
                retry_after=error.retry_after,
            )
        raise _rate_limit_error(error.retry_after) from error

    user = (
        await db.execute(
            select(AdminUser).where(
                or_(
                    func.lower(AdminUser.username) == normalized_username,
                    func.lower(AdminUser.email) == normalized_username,
                )
            )
        )
    ).scalars().first()
    encoded_password = (
        user.password_hash if user and user.is_active else DUMMY_PASSWORD_HASH
    )
    credentials_valid = verify_password(password, encoded_password)
    if not user or not user.is_active or not credentials_valid:
        retry_after = await throttle.register_failure(
            principal=normalized_username,
            client_address=client_address,
        )
        if retry_after:
            await record_security_event(
                db,
                action="security.admin_login_locked",
                severity="critical",
                source="admin_auth",
                actor_id=user.id if user else None,
                principal=normalized_username,
                client_address=client_address,
                resource_type="admin_user" if user else "admin_login",
                resource_id=user.id if user else None,
                details={
                    "retry_after_seconds": retry_after,
                    "known_principal": bool(user),
                },
                comment="Превышен лимит неверных попыток административного входа",
            )
        await db.commit()
        if _prefers_html(request):
            if retry_after:
                return _login_response(
                    error="Слишком много попыток входа. Повторите позже.",
                    username=username,
                    status_code=429,
                    retry_after=retry_after,
                )
            return _login_response(
                error="Неверный логин или пароль.",
                username=username,
                status_code=401,
            )
        if retry_after:
            raise _rate_limit_error(retry_after)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    await throttle.register_success(
        principal=normalized_username,
        client_address=client_address,
    )
    roles = normalize_roles(user.role)
    await record_security_event(
        db,
        action="security.admin_password_authenticated",
        severity="info",
        source="admin_auth",
        actor_id=user.id,
        principal=normalized_username,
        client_address=client_address,
        resource_type="admin_user",
        resource_id=user.id,
        details={
            "roles": roles,
            "mfa_required": ROLE_SUPERADMIN in roles,
        },
        comment="Пароль административного пользователя подтверждён",
    )
    await db.commit()

    if ROLE_SUPERADMIN in roles:
        purpose = "verify" if user.mfa_enabled else "setup"
        response = RedirectResponse(url=f"/mfa/{purpose}", status_code=303)
        _set_challenge_cookie(
            response,
            create_mfa_challenge_token(user.id, purpose),
        )
        response.delete_cookie(settings.admin_session_cookie, path="/")
        return response

    token = create_access_token(
        user.id,
        user.username or user.email,
        user.role,
        session_version=user.session_version,
        mfa_verified=False,
    )
    # All staff roles land on the canonical authenticated hub. Role-specific
    # workspaces are selected there; login no longer depends on an early
    # /admin-ui shadow route to rescue lawyer sessions.
    response = RedirectResponse(url="/operator", status_code=303)
    _set_session_cookie(response, token)
    return response


@router.get("/auth/session")
async def auth_session(request: Request, db: AsyncSession = Depends(get_db)):
    """Return browser identity metadata without exposing the bearer credential.

    The real session remains HttpOnly. Legacy staff JavaScript receives a
    non-secret sentinel; RequestOriginGuardMiddleware validates same-origin
    requests and bridges the cookie credential only inside the ASGI request
    scope. There is exactly one public GET /auth/session route.
    """

    token = request.cookies.get(settings.admin_session_cookie)
    payload = decode_access_token(token)
    if not payload or await is_token_revoked(db, token):
        raise HTTPException(status_code=401, detail="Требуется вход")
    return {
        "authenticated": True,
        "username": payload.get("username"),
        "role": payload.get("role"),
        "roles": payload.get("roles", [payload.get("role")]),
        "mfa_verified": bool(payload.get("mfa")),
        "api_token": BROWSER_SESSION_SENTINEL,
        "session_transport": "httponly_cookie",
    }


@router.post("/logout")
async def logout(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    token = x_admin_token or request.cookies.get(settings.admin_session_cookie)
    payload = decode_access_token(token)
    revoked = await revoke_token(
        db,
        token,
        reason="logout",
        comment="Пользователь завершил административную сессию",
    )
    if revoked and payload:
        try:
            actor_id = int(payload.get("uid") or 0) or None
        except (TypeError, ValueError):
            actor_id = None
        db.add(
            AuditLog(
                actor_type="admin_user",
                actor_id=actor_id,
                action="security.session_logged_out",
                entity_type="admin_session",
                entity_id=None,
                old_value=None,
                new_value={"token_revoked": True},
                comment="Токен отозван до окончания срока действия",
            )
        )
    await db.commit()
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(settings.admin_session_cookie, path="/")
    response.delete_cookie(MFA_CHALLENGE_COOKIE, path="/")
    return response


LOGIN_HTML = """
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Вход в Digital Legal Concierge</title>
<style>
:root{--ink:#172033;--muted:#667085;--line:#dfe3ea;--primary:#3157d5;--error:#b42318;--error-bg:#fef3f2}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:linear-gradient(145deg,#eef2ff,#f7f8fb 45%,#eef4ff);color:var(--ink);display:grid;place-items:center;min-height:100vh;padding:20px}
.card{background:#fff;border:1px solid var(--line);border-radius:22px;padding:28px;width:min(460px,96vw);box-shadow:0 20px 55px rgba(16,24,40,.12)}
.brand{display:flex;gap:12px;align-items:center;margin-bottom:18px}.mark{width:44px;height:44px;border-radius:14px;background:#111827;color:#fff;display:grid;place-items:center;font-size:24px}.brand h1{font-size:22px;margin:0}.brand p{margin:3px 0 0;color:var(--muted);font-size:13px}
label{display:block;font-weight:700;font-size:14px;margin-top:14px}input{width:100%;margin-top:7px;padding:12px 13px;border:1px solid #cfd5df;border-radius:12px;font-size:16px}input:focus{outline:3px solid #dfe6ff;border-color:var(--primary)}button{width:100%;border:0;border-radius:12px;background:var(--primary);color:#fff;padding:13px;font-weight:800;font-size:15px;margin-top:18px;cursor:pointer}button:hover{background:#2748bd}.muted{color:var(--muted);font-size:13px;line-height:1.5}.error{background:var(--error-bg);border:1px solid #fecdca;color:var(--error);padding:11px 12px;border-radius:12px;margin:12px 0;font-size:14px}.help{margin-top:18px;padding-top:16px;border-top:1px solid var(--line)}a{color:var(--primary)}
</style>
</head>
<body>
<form class="card" method="post" action="/login">
  <div class="brand"><div class="mark">⚖</div><div><h1>Вход в кабинет</h1><p>Digital Legal Concierge</p></div></div>
  <p class="muted">Используйте логин или email вашей учетной записи кабинета. Пароль из переменных сервера не заменяет пароль уже созданного пользователя.</p>
  __ERROR__
  <label for="username">Логин или email</label>
  <input id="username" name="username" value="__USERNAME__" autocomplete="username" required autofocus>
  <label for="password">Пароль</label>
  <input id="password" name="password" type="password" autocomplete="current-password" required>
  <button type="submit">Войти в кабинет</button>
  <div class="help muted">Для суперадминистратора после пароля потребуется код MFA. <a href="/operator">Вернуться в рабочее пространство</a>.</div>
</form>
</body>
</html>
"""
