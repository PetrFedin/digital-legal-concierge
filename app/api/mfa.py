from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO

import qrcode
from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.models.audit_log import AuditLog
from app.security.access_control import (
    ROLE_SUPERADMIN,
    create_access_token,
    decode_access_token,
    decode_mfa_challenge_token,
    normalize_roles,
    verify_password,
)
from app.security.mfa import (
    consume_recovery_code,
    consume_totp,
    decrypt_secret,
    encrypt_secret,
    generate_recovery_codes,
    generate_totp_secret,
    provisioning_uri,
    recovery_code_count,
    utcnow,
)

router = APIRouter(prefix="/mfa", tags=["mfa"])
CHALLENGE_COOKIE = "dlc_mfa_challenge"
MAX_MFA_ATTEMPTS = 5
MFA_LOCK_MINUTES = 15
NO_STORE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


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


def _clear_challenge(response) -> None:
    response.delete_cookie(CHALLENGE_COOKIE, path="/")


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


async def _challenge_user(
    request: Request,
    db: AsyncSession,
    purpose: str,
    *,
    for_update: bool = False,
) -> AdminUser:
    payload = decode_mfa_challenge_token(request.cookies.get(CHALLENGE_COOKIE))
    if not payload or payload.get("purpose") != purpose:
        raise HTTPException(401, "MFA challenge истёк. Выполните вход повторно")
    statement = select(AdminUser).where(
        AdminUser.id == int(payload.get("uid") or 0)
    )
    if for_update:
        statement = statement.with_for_update()
    user = (await db.execute(statement)).scalar_one_or_none()
    if (
        not user
        or not user.is_active
        or ROLE_SUPERADMIN not in normalize_roles(user.role)
    ):
        raise HTTPException(
            403,
            "MFA доступна только активному суперадминистратору",
        )
    return user


async def _authenticated_user(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
    *,
    for_update: bool = False,
) -> tuple[AdminUser, dict]:
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    payload = decode_access_token(token)
    if not payload or payload.get("legacy") or not payload.get("mfa"):
        raise HTTPException(401, "Требуется подтверждённая MFA-сессия")
    statement = select(AdminUser).where(
        AdminUser.id == int(payload.get("uid") or 0)
    )
    if for_update:
        statement = statement.with_for_update()
    user = (await db.execute(statement)).scalar_one_or_none()
    current_roles = normalize_roles(user.role) if user else []
    if (
        not user
        or not user.is_active
        or not user.mfa_enabled
        or ROLE_SUPERADMIN not in current_roles
    ):
        raise HTTPException(401, "MFA-сессия недействительна")
    if set(current_roles) != set(normalize_roles(payload.get("roles"))):
        raise HTTPException(401, "Права изменены. Выполните вход повторно")
    if int(payload.get("sv") or 0) != int(user.session_version or 1):
        raise HTTPException(401, "Сессия отозвана")
    return user, payload


async def _audit(
    db: AsyncSession,
    user: AdminUser,
    action: str,
    *,
    old_value: dict | None = None,
    new_value: dict | None = None,
    comment: str | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_type="admin_user",
            actor_id=user.id,
            action=action,
            entity_type="admin_user",
            entity_id=user.id,
            old_value=old_value,
            new_value=new_value,
            comment=comment,
        )
    )


def _check_lock(user: AdminUser) -> None:
    locked_until = _as_utc(user.mfa_locked_until)
    if locked_until and locked_until > utcnow():
        seconds = max(1, int((locked_until - utcnow()).total_seconds()))
        raise HTTPException(
            status_code=429,
            detail=f"MFA временно заблокирована. Повторите через {seconds} сек.",
            headers={"Retry-After": str(seconds)},
        )


async def _register_failure(db: AsyncSession, user: AdminUser) -> None:
    user.mfa_failed_attempts = int(user.mfa_failed_attempts or 0) + 1
    if user.mfa_failed_attempts >= MAX_MFA_ATTEMPTS:
        user.mfa_locked_until = utcnow() + timedelta(minutes=MFA_LOCK_MINUTES)
        await _audit(
            db,
            user,
            "security.mfa_locked",
            new_value={"locked_until": user.mfa_locked_until.isoformat()},
            comment="Превышено число неверных MFA-кодов",
        )
    await db.commit()


def _issue_session(user: AdminUser) -> str:
    return create_access_token(
        user.id,
        user.username or user.email,
        user.role,
        session_version=user.session_version,
        mfa_verified=True,
    )


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _challenge_user(request, db, "setup", for_update=True)
    if user.mfa_enabled:
        return RedirectResponse("/admin-ui", status_code=303)
    secret = decrypt_secret(user.mfa_secret_encrypted)
    if not secret:
        secret = generate_totp_secret()
        user.mfa_secret_encrypted = encrypt_secret(secret)
        user.mfa_last_totp_step = None
        await _audit(db, user, "security.mfa_setup_started")
        await db.commit()
    return HTMLResponse(_setup_html(secret), headers=NO_STORE_HEADERS)


@router.get("/qr")
async def setup_qr(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _challenge_user(request, db, "setup")
    secret = decrypt_secret(user.mfa_secret_encrypted)
    if not secret:
        raise HTTPException(409, "Сначала откройте страницу настройки MFA")
    image = qrcode.make(provisioning_uri(user, secret))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="image/png",
        headers=NO_STORE_HEADERS,
    )


@router.post("/setup", response_class=HTMLResponse)
async def confirm_setup(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await _challenge_user(request, db, "setup", for_update=True)
    _check_lock(user)
    secret = decrypt_secret(user.mfa_secret_encrypted)
    if not consume_totp(user, secret, code):
        await _register_failure(db, user)
        raise HTTPException(401, "Неверный или уже использованный код")

    codes, encoded_codes = generate_recovery_codes(user.id)
    old_version = int(user.session_version or 1)
    user.mfa_enabled = True
    user.mfa_confirmed_at = utcnow()
    user.mfa_recovery_codes = encoded_codes
    user.mfa_recovery_codes_generated_at = utcnow()
    user.mfa_failed_attempts = 0
    user.mfa_locked_until = None
    user.session_version = old_version + 1
    await _audit(
        db,
        user,
        "security.mfa_enabled",
        old_value={"session_version": old_version},
        new_value={
            "session_version": user.session_version,
            "recovery_codes": len(codes),
        },
    )
    await db.commit()

    response = HTMLResponse(
        _recovery_codes_html(codes, "MFA включена"),
        headers=NO_STORE_HEADERS,
    )
    _set_session_cookie(response, _issue_session(user))
    _clear_challenge(response)
    return response


@router.get("/verify", response_class=HTMLResponse)
async def verify_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _challenge_user(request, db, "verify")
    if not user.mfa_enabled:
        return RedirectResponse("/mfa/setup", status_code=303)
    return HTMLResponse(VERIFY_HTML, headers=NO_STORE_HEADERS)


@router.post("/verify")
async def verify_login(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await _challenge_user(request, db, "verify", for_update=True)
    _check_lock(user)
    secret = decrypt_secret(user.mfa_secret_encrypted)
    is_totp = consume_totp(user, secret, code)
    used_recovery = False
    if not is_totp:
        used_recovery = consume_recovery_code(user, code)
    if not is_totp and not used_recovery:
        await _register_failure(db, user)
        raise HTTPException(401, "Неверный, использованный или просроченный код")

    user.mfa_failed_attempts = 0
    user.mfa_locked_until = None
    await _audit(
        db,
        user,
        "security.mfa_login",
        new_value={
            "method": "recovery_code" if used_recovery else "totp",
            "recovery_codes_remaining": recovery_code_count(
                user.mfa_recovery_codes
            ),
        },
    )
    await db.commit()
    response = RedirectResponse("/admin-ui", status_code=303)
    _set_session_cookie(response, _issue_session(user))
    _clear_challenge(response)
    return response


@router.get("/manage", response_class=HTMLResponse)
async def manage_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    user, _ = await _authenticated_user(request, db, x_admin_token)
    return HTMLResponse(
        _manage_html(user, recovery_code_count(user.mfa_recovery_codes)),
        headers=NO_STORE_HEADERS,
    )


@router.post("/recovery/regenerate", response_class=HTMLResponse)
async def regenerate_recovery_codes(
    request: Request,
    password: str = Form(...),
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    user, _ = await _authenticated_user(
        request,
        db,
        x_admin_token,
        for_update=True,
    )
    _check_lock(user)
    if not verify_password(password, user.password_hash) or not consume_totp(
        user,
        decrypt_secret(user.mfa_secret_encrypted),
        code,
    ):
        await _register_failure(db, user)
        raise HTTPException(401, "Пароль или MFA-код неверен либо уже использован")
    codes, encoded_codes = generate_recovery_codes(user.id)
    user.mfa_recovery_codes = encoded_codes
    user.mfa_recovery_codes_generated_at = utcnow()
    user.mfa_failed_attempts = 0
    user.mfa_locked_until = None
    user.session_version = int(user.session_version or 1) + 1
    await _audit(
        db,
        user,
        "security.mfa_recovery_codes_rotated",
        new_value={
            "recovery_codes": len(codes),
            "session_version": user.session_version,
        },
    )
    await db.commit()
    response = HTMLResponse(
        _recovery_codes_html(codes, "Резервные коды обновлены"),
        headers=NO_STORE_HEADERS,
    )
    _set_session_cookie(response, _issue_session(user))
    return response


@router.post("/sessions/rotate")
async def rotate_sessions(
    request: Request,
    password: str = Form(...),
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    user, _ = await _authenticated_user(
        request,
        db,
        x_admin_token,
        for_update=True,
    )
    _check_lock(user)
    if not verify_password(password, user.password_hash) or not consume_totp(
        user,
        decrypt_secret(user.mfa_secret_encrypted),
        code,
    ):
        await _register_failure(db, user)
        raise HTTPException(401, "Пароль или MFA-код неверен либо уже использован")
    old_version = int(user.session_version or 1)
    user.session_version = old_version + 1
    user.mfa_failed_attempts = 0
    user.mfa_locked_until = None
    await _audit(
        db,
        user,
        "security.sessions_rotated",
        old_value={"session_version": old_version},
        new_value={"session_version": user.session_version},
        comment="Все другие административные сессии отозваны",
    )
    await db.commit()
    response = RedirectResponse("/mfa/manage", status_code=303)
    _set_session_cookie(response, _issue_session(user))
    return response


def _base_style() -> str:
    return """
    body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f3f4f6;color:#111827;display:grid;place-items:center;min-height:100vh}
    .card{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:24px;width:min(560px,92vw);box-shadow:0 8px 30px rgba(0,0,0,.08)}
    input{width:100%;box-sizing:border-box;margin:8px 0 14px;padding:12px;border:1px solid #d1d5db;border-radius:12px}
    button,.button{display:inline-block;border:0;border-radius:12px;background:#111827;color:#fff;padding:12px 16px;font-weight:800;text-decoration:none;cursor:pointer}
    .muted{color:#6b7280;font-size:13px}.codes{display:grid;grid-template-columns:1fr 1fr;gap:8px;background:#f9fafb;padding:14px;border-radius:12px;font-family:monospace}.danger{color:#b91c1c}
    """


def _setup_html(secret: str) -> str:
    return f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Настройка MFA</title><style>{_base_style()}</style></head><body><main class='card'><h1>Обязательная MFA</h1><p>Добавьте учётную запись в Google Authenticator, Microsoft Authenticator, 1Password или другом TOTP-приложении.</p><p><img src='/mfa/qr' width='240' height='240' alt='QR-код MFA'></p><p class='muted'>Ключ для ручного ввода:</p><p><code>{secret}</code></p><form method='post' action='/mfa/setup'><label>Код из приложения</label><input name='code' inputmode='numeric' autocomplete='one-time-code' minlength='6' maxlength='6' required><button>Подтвердить и включить MFA</button></form></main></body></html>"""


def _recovery_codes_html(codes: list[str], title: str) -> str:
    items = "".join(f"<div>{code}</div>" for code in codes)
    return f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{title}</title><style>{_base_style()}</style></head><body><main class='card'><h1>{title}</h1><p class='danger'><b>Сохраните коды сейчас.</b> Повторно они показаны не будут. Каждый код используется один раз.</p><div class='codes'>{items}</div><p><a class='button' href='/admin-ui'>Перейти в админку</a></p></main></body></html>"""


def _manage_html(user: AdminUser, codes_left: int) -> str:
    confirmed = user.mfa_confirmed_at.isoformat() if user.mfa_confirmed_at else "—"
    return f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Безопасность аккаунта</title><style>{_base_style()}</style></head><body><main class='card'><h1>Безопасность аккаунта</h1><p><b>MFA:</b> включена</p><p><b>Подтверждена:</b> {confirmed}</p><p><b>Резервных кодов:</b> {codes_left}</p><h2>Обновить резервные коды</h2><form method='post' action='/mfa/recovery/regenerate'><input name='password' type='password' autocomplete='current-password' placeholder='Текущий пароль' required><input name='code' inputmode='numeric' autocomplete='one-time-code' placeholder='Новый код TOTP' required><button>Создать новые коды</button></form><h2>Завершить остальные сессии</h2><p class='muted'>Текущая вкладка останется авторизованной, все другие токены пользователя станут недействительными.</p><form method='post' action='/mfa/sessions/rotate'><input name='password' type='password' autocomplete='current-password' placeholder='Текущий пароль' required><input name='code' inputmode='numeric' autocomplete='one-time-code' placeholder='Новый код TOTP' required><button>Отозвать остальные сессии</button></form><p><a href='/admin-ui'>Вернуться в админку</a></p></main></body></html>"""


VERIFY_HTML = f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Подтверждение MFA</title><style>{_base_style()}</style></head><body><form class='card' method='post' action='/mfa/verify'><h1>Подтверждение входа</h1><p>Введите шестизначный код приложения-аутентификатора или одноразовый резервный код.</p><input name='code' autocomplete='one-time-code' required autofocus><button>Подтвердить</button></form></body></html>"""
