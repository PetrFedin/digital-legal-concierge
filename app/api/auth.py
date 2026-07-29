from __future__ import annotations

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


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(LOGIN_HTML, headers=NO_STORE_HEADERS)


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
    response = RedirectResponse(url="/admin-ui", status_code=303)
    _set_session_cookie(response, token)
    return response


@router.get("/auth/session")
async def auth_session(request: Request, db: AsyncSession = Depends(get_db)):
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
        "api_token": token,
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
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Вход в Digital Legal Concierge</title><style>
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f3f4f6;color:#111827;display:grid;place-items:center;min-height:100vh}.card{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:24px;width:min(440px,92vw);box-shadow:0 8px 30px rgba(0,0,0,.08)}input{width:100%;box-sizing:border-box;margin:8px 0 14px;padding:12px;border:1px solid #d1d5db;border-radius:12px}button{width:100%;border:0;border-radius:12px;background:#111827;color:#fff;padding:12px;font-weight:800}.muted{color:#6b7280;font-size:13px}</style></head>
<body><form class="card" method="post" action="/login"><h1>⚖ Вход в админку</h1><p class="muted">Введите логин или email и пароль. Для суперадминистраторов после пароля обязательна проверка TOTP или резервным кодом.</p><label>Логин или email</label><input name="username" autocomplete="username" required autofocus><label>Пароль</label><input name="password" type="password" autocomplete="current-password" required><button>Войти</button></form></body></html>
"""
