from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.models.audit_log import AuditLog
from app.models.lawyer import Lawyer
from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_LAWYER,
    ROLE_OPERATOR,
    ROLE_SUPERADMIN,
    ROLE_TESTER,
    VALID_ROLES,
    hash_password,
    normalize_roles,
    serialize_roles,
)
from app.security.document_access import DocumentAccessError, resolve_document_actor
from app.security.mfa import recovery_code_count

router = APIRouter(prefix="/access", tags=["access-management"])
PRODUCT_WORKSPACE_ROLES = frozenset({ROLE_ADMIN, ROLE_SUPERADMIN, ROLE_LAWYER})


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _require_superadmin(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    actor = await resolve_document_actor(db, _token(request, header_token))
    if actor.role != ROLE_SUPERADMIN:
        raise HTTPException(status_code=403, detail="Доступ только для суперадминистратора")
    return actor


async def _superadmin_ui_or_redirect(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    try:
        return await _require_superadmin(request, db, header_token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        if error.status_code in {403, 409}:
            return RedirectResponse(url="/operator", status_code=303)
        raise
    except HTTPException as error:
        if error.status_code in {403, 409}:
            return RedirectResponse(url="/operator", status_code=303)
        raise


def _has_product_role_conflict(roles: list[str]) -> bool:
    return ROLE_LAWYER in roles and (
        ROLE_ADMIN in roles or ROLE_SUPERADMIN in roles
    )


def _validate_workspace_roles(roles: list[str]) -> None:
    if not roles:
        raise HTTPException(400, "Назначьте хотя бы одну допустимую роль")
    if any(role not in VALID_ROLES for role in roles):
        raise HTTPException(400, "Недопустимая роль")
    if not PRODUCT_WORKSPACE_ROLES.intersection(roles):
        raise HTTPException(
            400,
            (
                "Роли «Оператор» и «Тестировщик» являются дополнительными. "
                "Добавьте базовую роль «Администратор», «Суперадминистратор» или «Юрист»."
            ),
        )
    if _has_product_role_conflict(roles):
        raise HTTPException(
            400,
            (
                "Нельзя совмещать административную и юридическую ответственность в одной "
                "персональной учётной записи. Создайте отдельный профиль юриста."
            ),
        )


def parse_roles(payload: dict, current: str | None = None) -> list[str]:
    raw = payload.get("roles", payload.get("role", current))
    roles = normalize_roles(raw)
    _validate_workspace_roles(roles)
    return roles


async def active_superadmin_count(
    db: AsyncSession,
    exclude_user_id: int | None = None,
) -> int:
    rows = list(
        (
            await db.execute(select(AdminUser).where(AdminUser.is_active.is_(True)))
        ).scalars().all()
    )
    return sum(
        1
        for row in rows
        if row.id != exclude_user_id
        and ROLE_SUPERADMIN in normalize_roles(row.role)
    )


async def sync_lawyer(db: AsyncSession, user: AdminUser, roles: list[str]) -> None:
    lawyer = None
    if user.email:
        lawyer = (
            await db.execute(select(Lawyer).where(Lawyer.email == user.email))
        ).scalars().first()
    if ROLE_LAWYER in roles:
        if lawyer is None:
            lawyer = Lawyer(
                full_name=user.full_name,
                email=user.email,
                specialization="ДДУ 214-ФЗ",
                is_active=user.is_active,
            )
            db.add(lawyer)
        else:
            lawyer.full_name = user.full_name
            lawyer.is_active = user.is_active
    elif lawyer is not None:
        lawyer.is_active = False


def user_snapshot(user: AdminUser) -> dict:
    return {
        "id": int(user.id),
        "full_name": user.full_name,
        "username": user.username,
        "email": user.email,
        "telegram_id": user.telegram_id,
        "roles": normalize_roles(user.role),
        "is_active": bool(user.is_active),
        "mfa_enabled": bool(user.mfa_enabled),
        "mfa_confirmed_at": (
            user.mfa_confirmed_at.isoformat() if user.mfa_confirmed_at else None
        ),
        "recovery_codes_remaining": recovery_code_count(user.mfa_recovery_codes),
        "session_version": int(user.session_version or 1),
    }


async def write_audit(
    db: AsyncSession,
    *,
    actor_id: int,
    action: str,
    user: AdminUser,
    old_value: dict | None,
    new_value: dict | None,
    comment: str | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_type="admin_user",
            actor_id=int(actor_id),
            action=action,
            entity_type="admin_user",
            entity_id=int(user.id),
            old_value=old_value,
            new_value=new_value,
            comment=comment,
        )
    )


async def _historical_role_conflicts(db: AsyncSession) -> list[AdminUser]:
    rows = list(
        (
            await db.execute(select(AdminUser).order_by(AdminUser.id.asc()))
        ).scalars().all()
    )
    return [
        user
        for user in rows
        if _has_product_role_conflict(normalize_roles(user.role))
    ]


@router.get("/users")
async def list_users(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _require_superadmin(request, db, x_admin_token)
    rows = list(
        (
            await db.execute(select(AdminUser).order_by(AdminUser.id.asc()))
        ).scalars().all()
    )
    return [
        {
            **user_snapshot(user),
            "role": user.role,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "role_conflict": _has_product_role_conflict(normalize_roles(user.role)),
        }
        for user in rows
    ]


@router.post("/users")
async def create_user(
    request: Request,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _require_superadmin(request, db, x_admin_token)
    username = str(payload.get("username") or "").strip()
    email = str(payload.get("email") or "").strip().lower()
    full_name = str(payload.get("full_name") or "").strip()
    password = str(payload.get("password") or "")
    roles = parse_roles(payload)
    if not username or not email or not full_name:
        raise HTTPException(400, "Имя, логин и email обязательны")
    if len(password) < 8:
        raise HTTPException(400, "Пароль должен содержать не менее 8 символов")

    duplicate = (
        await db.execute(
            select(AdminUser).where(
                or_(AdminUser.username == username, AdminUser.email == email)
            )
        )
    ).scalars().first()
    if duplicate:
        raise HTTPException(409, "Логин или email уже используется")

    telegram_id = payload.get("telegram_id")
    if telegram_id not in (None, ""):
        tg_duplicate = (
            await db.execute(
                select(AdminUser).where(AdminUser.telegram_id == int(telegram_id))
            )
        ).scalars().first()
        if tg_duplicate:
            raise HTTPException(409, "Этот Telegram ID уже связан с другим пользователем")

    user = AdminUser(
        full_name=full_name,
        username=username,
        email=email,
        telegram_id=int(telegram_id) if telegram_id not in (None, "") else None,
        password_hash=hash_password(password),
        role=serialize_roles(roles),
        is_active=bool(payload.get("is_active", True)),
        session_version=1,
    )
    db.add(user)
    await db.flush()
    await sync_lawyer(db, user, roles)
    await write_audit(
        db,
        actor_id=actor.account_id,
        action="access.user_created",
        user=user,
        old_value=None,
        new_value=user_snapshot(user),
    )
    await db.commit()
    return {
        "ok": True,
        "id": int(user.id),
        "username": user.username,
        "roles": roles,
        "mfa_setup_required": ROLE_SUPERADMIN in roles,
    }


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int,
    request: Request,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _require_superadmin(request, db, x_admin_token)
    user = (
        await db.execute(
            select(AdminUser).where(AdminUser.id == int(user_id)).with_for_update()
        )
    ).scalars().first()
    if user is None:
        raise HTTPException(404, "Пользователь не найден")

    old = user_snapshot(user)
    old_roles = normalize_roles(user.role)
    roles = (
        parse_roles(payload, user.role)
        if "roles" in payload or "role" in payload
        else old_roles
    )
    new_active = bool(payload.get("is_active", user.is_active))
    removes_superadmin = ROLE_SUPERADMIN in old_roles and (
        ROLE_SUPERADMIN not in roles or not new_active
    )
    if removes_superadmin and await active_superadmin_count(
        db,
        exclude_user_id=int(user.id),
    ) == 0:
        raise HTTPException(
            409,
            "Нельзя отключить или лишить прав последнего суперадминистратора",
        )

    session_sensitive_change = False
    if "roles" in payload or "role" in payload:
        user.role = serialize_roles(roles)
        session_sensitive_change = set(old_roles) != set(roles)
    if "is_active" in payload:
        user.is_active = new_active
        session_sensitive_change = session_sensitive_change or old["is_active"] != new_active
    if payload.get("full_name"):
        user.full_name = str(payload["full_name"]).strip()
    if "telegram_id" in payload:
        telegram_id = payload["telegram_id"]
        if telegram_id not in (None, ""):
            tg_duplicate = (
                await db.execute(
                    select(AdminUser).where(
                        AdminUser.telegram_id == int(telegram_id),
                        AdminUser.id != int(user.id),
                    )
                )
            ).scalars().first()
            if tg_duplicate:
                raise HTTPException(409, "Этот Telegram ID уже связан с другим пользователем")
            user.telegram_id = int(telegram_id)
        else:
            user.telegram_id = None
    if payload.get("password"):
        user.password_hash = hash_password(str(payload["password"]))
        session_sensitive_change = True
    if session_sensitive_change:
        user.session_version = int(user.session_version or 1) + 1

    await sync_lawyer(db, user, roles)
    await write_audit(
        db,
        actor_id=actor.account_id,
        action="access.user_updated",
        user=user,
        old_value=old,
        new_value=user_snapshot(user),
        comment=(
            "Активные сессии пользователя отозваны"
            if session_sensitive_change
            else None
        ),
    )
    await db.commit()
    return {
        "ok": True,
        "id": int(user.id),
        "roles": roles,
        "is_active": bool(user.is_active),
        "sessions_revoked": session_sensitive_change,
    }


@router.post("/users/{user_id}/mfa/reset")
async def reset_user_mfa(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _require_superadmin(request, db, x_admin_token)
    user = (
        await db.execute(
            select(AdminUser).where(AdminUser.id == int(user_id)).with_for_update()
        )
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, "Пользователь не найден")
    if ROLE_SUPERADMIN not in normalize_roles(user.role):
        raise HTTPException(409, "MFA обязательна только для суперадминистратора")

    old = user_snapshot(user)
    user.mfa_enabled = False
    user.mfa_secret_encrypted = None
    user.mfa_confirmed_at = None
    user.mfa_recovery_codes = None
    user.mfa_recovery_codes_generated_at = None
    user.mfa_failed_attempts = 0
    user.mfa_locked_until = None
    user.session_version = int(user.session_version or 1) + 1
    await write_audit(
        db,
        actor_id=actor.account_id,
        action="security.mfa_reset_by_superadmin",
        user=user,
        old_value=old,
        new_value=user_snapshot(user),
        comment="Все сессии отозваны; при следующем входе требуется новая настройка MFA",
    )
    await db.commit()
    return {"ok": True, "user_id": int(user.id), "mfa_setup_required": True}


@router.get("/ui", response_class=HTMLResponse)
async def access_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    gate = await _superadmin_ui_or_redirect(request, db, x_admin_token)
    if isinstance(gate, RedirectResponse):
        return gate
    conflicts = await _historical_role_conflicts(db)
    warning = ""
    if conflicts:
        rows = "".join(
            f"<li>#{int(user.id)} {escape(user.full_name or user.username or 'Сотрудник')} — {escape(', '.join(normalize_roles(user.role)))}</li>"
            for user in conflicts
        )
        warning = (
            '<section class="warning"><b>⚠ Требуется разделить исторические конфликтующие роли</b>'
            f"<ul>{rows}</ul><p>Система не меняет такие записи автоматически: разделение ответственности должно быть подтверждено суперадминистратором.</p></section>"
        )
    return HTMLResponse(ACCESS_HTML.replace("__ROLE_WARNING__", warning))


ACCESS_HTML = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Пользователи и права</title>
<style>:root{--bg:#f4f6fa;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--red:#b42318;--amber:#a15c00}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}header{background:#111827;color:#fff;padding:18px 24px;display:flex;justify-content:space-between;gap:12px;align-items:center}header a{color:#fff}main{max-width:1160px;margin:auto;padding:20px}.card,.warning{background:#fff;border:1px solid var(--line);border-radius:16px;padding:17px;margin-bottom:14px}.warning{border-color:#fedf89;background:#fffbeb;color:#7a2e0e}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}label{font-size:13px;font-weight:700}input{width:100%;margin-top:5px;padding:10px;border:1px solid #d0d5dd;border-radius:9px}.roles{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}.roles label{display:flex;align-items:center;gap:5px}.roles input{width:auto;margin:0}button{border:0;border-radius:9px;padding:9px 11px;background:var(--blue);color:#fff;font-weight:750;cursor:pointer}.danger{background:var(--red)}.muted{color:var(--muted);font-size:13px}.user{border-top:1px solid var(--line);padding:12px 0}.chips{display:flex;gap:5px;flex-wrap:wrap;margin:6px 0}.chip{background:#eef2ff;border-radius:999px;padding:4px 7px;font-size:12px}.conflict{color:var(--red);font-weight:750}@media(max-width:680px){.grid{grid-template-columns:1fr}header{align-items:flex-start;flex-direction:column}main{padding:12px}}</style></head><body>
<header><b>⚖ Пользователи, роли и MFA</b><div><a href="/operator">Рабочее пространство</a></div></header><main>
__ROLE_WARNING__
<section class="card"><h2>Добавить сотрудника</h2><p class="muted">Одна персональная учётная запись — одна базовая продуктовая ответственность. Технические роли можно добавлять дополнительно.</p><div class="grid"><label>ФИО<input id="full_name"></label><label>Логин<input id="username"></label><label>Email<input id="email" type="email"></label><label>Telegram ID<input id="telegram_id"></label><label>Пароль<input id="password" type="password"></label></div><div class="roles"><label><input type="checkbox" id="new_admin" checked>Администратор</label><label><input type="checkbox" id="new_superadmin">Суперадминистратор</label><label><input type="checkbox" id="new_lawyer">Юрист</label><label><input type="checkbox" id="new_operator">Оператор</label><label><input type="checkbox" id="new_tester">Тестировщик</label></div><button onclick="createUser(this)">Создать пользователя</button><p id="message" class="muted"></p></section>
<section class="card"><h2>Текущие пользователи</h2><div id="users">Загрузка…</div></section></main>
<script>
let token=''; const roleIds={admin:'new_admin',superadmin:'new_superadmin',lawyer:'new_lawyer',operator:'new_operator',tester:'new_tester'};
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function boot(){const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();if(!(s.roles||[]).includes('superadmin')){location.href='/operator';return}token=s.api_token||'';await loadUsers()}
async function api(path,options={}){const headers={'x-admin-token':token,'content-type':'application/json',...(options.headers||{})};const r=await fetch(path,{credentials:'same-origin',cache:'no-store',...options,headers});let d={};try{d=await r.json()}catch(e){}if(!r.ok)throw new Error(d.detail||'Ошибка запроса');return d}
function selectedRoles(){return Object.entries(roleIds).filter(([,id])=>document.getElementById(id).checked).map(([role])=>role)}
async function createUser(btn){btn.disabled=true;const msg=document.getElementById('message');msg.textContent='Сохраняем…';try{const payload={full_name:full_name.value.trim(),username:username.value.trim(),email:email.value.trim(),telegram_id:telegram_id.value.trim()||null,password:password.value,roles:selectedRoles()};await api('/access/users',{method:'POST',body:JSON.stringify(payload)});msg.textContent='Пользователь создан.';await loadUsers()}catch(e){msg.textContent=e.message}finally{btn.disabled=false}}
async function loadUsers(){const rows=await api('/access/users');users.innerHTML=rows.map(u=>`<article class="user"><b>#${u.id} ${esc(u.full_name||u.username)}</b>${u.role_conflict?'<div class="conflict">Конфликт продуктовых ролей — требуется разделение учётной записи</div>':''}<div class="chips">${(u.roles||[]).map(x=>`<span class="chip">${esc(x)}</span>`).join('')}</div><div class="muted">${esc(u.email||'')} · ${u.is_active?'активен':'отключён'} · MFA: ${u.mfa_enabled?'включена':'нет'}</div></article>`).join('')||'<p class="muted">Пользователей нет.</p>'}
boot().catch(e=>{users.textContent=e.message});
</script></body></html>
"""

__all__ = [
    "PRODUCT_WORKSPACE_ROLES",
    "parse_roles",
    "router",
]
