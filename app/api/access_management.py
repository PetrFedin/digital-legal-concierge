from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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
    decode_access_token,
    hash_password,
    normalize_roles,
    serialize_roles,
)
from app.security.mfa import recovery_code_count

router = APIRouter(prefix="/access", tags=["access-management"])


def require_superadmin(token: str | None) -> dict:
    payload = decode_access_token(token)
    if not payload or ROLE_SUPERADMIN not in normalize_roles(payload.get("roles")):
        raise HTTPException(status_code=403, detail="Доступ только для суперадминистратора")
    if not payload.get("legacy") and not payload.get("mfa"):
        raise HTTPException(status_code=403, detail="Требуется подтверждённая MFA-сессия")
    return payload


def parse_roles(payload: dict, current: str | None = None) -> list[str]:
    raw = payload.get("roles", payload.get("role", current))
    roles = normalize_roles(raw)
    if not roles:
        raise HTTPException(400, "Назначьте хотя бы одну роль")
    if any(role not in VALID_ROLES for role in roles):
        raise HTTPException(400, "Недопустимая роль")
    return roles


async def active_superadmin_count(
    db: AsyncSession, exclude_user_id: int | None = None
) -> int:
    rows = (
        await db.execute(select(AdminUser).where(AdminUser.is_active.is_(True)))
    ).scalars().all()
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
        if not lawyer:
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
    elif lawyer:
        lawyer.is_active = False


async def write_audit(
    db: AsyncSession,
    actor: dict,
    action: str,
    user: AdminUser,
    old_value: dict | None,
    new_value: dict | None,
    comment: str | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_type="admin_user",
            actor_id=int(actor.get("uid") or 0) or None,
            action=action,
            entity_type="admin_user",
            entity_id=user.id,
            old_value=old_value,
            new_value=new_value,
            comment=comment,
        )
    )


def user_snapshot(user: AdminUser) -> dict:
    return {
        "id": user.id,
        "full_name": user.full_name,
        "username": user.username,
        "email": user.email,
        "telegram_id": user.telegram_id,
        "roles": normalize_roles(user.role),
        "is_active": user.is_active,
        "mfa_enabled": bool(user.mfa_enabled),
        "mfa_confirmed_at": (
            user.mfa_confirmed_at.isoformat() if user.mfa_confirmed_at else None
        ),
        "recovery_codes_remaining": recovery_code_count(user.mfa_recovery_codes),
        "session_version": int(user.session_version or 1),
    }


@router.get("/users")
async def list_users(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_superadmin(x_admin_token)
    rows = (
        await db.execute(select(AdminUser).order_by(AdminUser.id.asc()))
    ).scalars().all()
    return [
        {
            **user_snapshot(user),
            "role": user.role,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        }
        for user in rows
    ]


@router.post("/users")
async def create_user(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = require_superadmin(x_admin_token)
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
    await write_audit(db, actor, "access.user_created", user, None, user_snapshot(user))
    await db.commit()
    await db.refresh(user)
    return {
        "ok": True,
        "id": user.id,
        "username": user.username,
        "roles": roles,
        "mfa_setup_required": ROLE_SUPERADMIN in roles,
    }


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = require_superadmin(x_admin_token)
    user = (
        await db.execute(
            select(AdminUser).where(AdminUser.id == user_id).with_for_update()
        )
    ).scalars().first()
    if not user:
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
    if (
        removes_superadmin
        and await active_superadmin_count(db, exclude_user_id=user.id) == 0
    ):
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
                        AdminUser.id != user.id,
                    )
                )
            ).scalars().first()
            if tg_duplicate:
                raise HTTPException(
                    409,
                    "Этот Telegram ID уже связан с другим пользователем",
                )
            user.telegram_id = int(telegram_id)
        else:
            user.telegram_id = None
    if payload.get("password"):
        user.password_hash = hash_password(str(payload["password"]))
        session_sensitive_change = True
    if session_sensitive_change:
        user.session_version = int(user.session_version or 1) + 1

    await sync_lawyer(db, user, roles)
    new = user_snapshot(user)
    await write_audit(
        db,
        actor,
        "access.user_updated",
        user,
        old,
        new,
        comment=(
            "Активные сессии пользователя отозваны"
            if session_sensitive_change
            else None
        ),
    )
    await db.commit()
    return {
        "ok": True,
        "id": user.id,
        "roles": roles,
        "is_active": user.is_active,
        "sessions_revoked": session_sensitive_change,
    }


@router.post("/users/{user_id}/mfa/reset")
async def reset_user_mfa(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = require_superadmin(x_admin_token)
    user = (
        await db.execute(
            select(AdminUser).where(AdminUser.id == user_id).with_for_update()
        )
    ).scalar_one_or_none()
    if not user:
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
        actor,
        "security.mfa_reset_by_superadmin",
        user,
        old,
        user_snapshot(user),
        comment="Все сессии отозваны; при следующем входе требуется новая настройка MFA",
    )
    await db.commit()
    return {"ok": True, "user_id": user.id, "mfa_setup_required": True}


@router.get("/ui", response_class=HTMLResponse)
async def access_ui():
    return HTMLResponse(ACCESS_HTML)


ACCESS_HTML = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Пользователи и права</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f4f5f7;margin:0;color:#111827}header{background:#111827;color:#fff;padding:18px 24px;display:flex;justify-content:space-between;align-items:center}main{padding:24px;max-width:1280px;margin:auto}.card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:16px}input{width:100%;padding:10px;border:1px solid #d1d5db;border-radius:9px;box-sizing:border-box}label{font-size:13px;font-weight:700}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.roles{display:flex;gap:14px;flex-wrap:wrap;padding:12px 0}.roles label{display:flex;align-items:center;gap:6px}.roles input{width:auto}button{border:0;border-radius:9px;padding:10px 13px;background:#2563eb;color:#fff;font-weight:700;cursor:pointer}button:disabled{opacity:.55;cursor:wait}.danger{background:#dc2626}.secondary{background:#4b5563}.warning{background:#ca8a04}table{width:100%;border-collapse:collapse}td,th{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}.muted{color:#6b7280;font-size:13px}.badge{display:inline-block;background:#e5e7eb;border-radius:999px;padding:4px 8px;margin:2px;font-size:12px}.ok{color:#15803d}.bad{color:#b91c1c}@media(max-width:800px){.grid{grid-template-columns:1fr}table{font-size:12px}}</style></head>
<body><header><b>⚖ Пользователи, роли и MFA</b><div><a href="/mfa/manage" style="color:#fff;margin-right:16px">Моя безопасность</a><a href="/admin-ui" style="color:#fff">Админка</a></div></header><main>
<div class="card"><h2>Добавить пользователя</h2><div class="grid"><div><label>ФИО</label><input id="full_name"></div><div><label>Логин</label><input id="username"></div><div><label>Email</label><input id="email" type="email"></div><div><label>Telegram ID</label><input id="telegram_id"></div><div><label>Пароль (минимум 8 символов)</label><input id="password" type="password"></div></div><h3>Права</h3><div class="roles"><label><input type="checkbox" id="new_admin" checked> Администратор</label><label><input type="checkbox" id="new_superadmin"> Суперадминистратор</label><label><input type="checkbox" id="new_lawyer"> Юрист</label><label><input type="checkbox" id="new_operator"> Оператор</label><label><input type="checkbox" id="new_tester"> Тестировщик</label></div><p class="muted">Для каждого суперадминистратора MFA обязательна и настраивается при первом входе.</p><p><button onclick="createUser(this)">Создать пользователя</button></p><div id="message" class="muted" role="status" aria-live="polite"></div></div>
<div class="card"><h2>Текущие пользователи</h2><div id="usersMessage" class="muted" role="status" aria-live="polite"></div><div id="users">Загрузка…</div></div></main>
<script>
let token=''; const roleNames=['admin','superadmin','lawyer','operator','tester']; const labels={admin:'Администратор',superadmin:'Суперадминистратор',lawyer:'Юрист',operator:'Оператор',tester:'Тестировщик'};
async function boot(){const r=await fetch('/auth/session');if(!r.ok){location.href='/login';return}const s=await r.json();if(!(s.roles||[s.role]).includes('superadmin')||!s.mfa_verified){document.body.innerHTML='<main><div class="card"><h2>Недостаточно прав</h2><p>Раздел доступен только суперадминистратору с подтверждённой MFA.</p><a href="/admin-ui">Вернуться</a></div></main>';return}token=s.api_token;await loadUsers()}
async function api(path,opts={}){const r=await fetch(path,{...opts,headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка');return d}
async function withButton(button,work){if(button&&button.disabled)return;const label=button?button.textContent:'';if(button){button.disabled=true;button.textContent='Выполняется…'}try{return await work()}finally{if(button){button.disabled=false;button.textContent=label}}}
function feedback(target,text,ok){target.textContent=text;target.className=ok?'muted ok':'muted bad'}
function roleChecks(u){return roleNames.map(r=>`<label><input type="checkbox" id="r_${u.id}_${r}" ${u.roles.includes(r)?'checked':''}> ${labels[r]}</label>`).join('')}
async function loadUsers(){const rows=await api('/access/users');users.innerHTML='<table><tr><th>Пользователь</th><th>Права</th><th>MFA</th><th>Статус</th><th>Действия</th></tr>'+rows.map(u=>`<tr><td><b>${esc(u.full_name)}</b><br><span class="muted">${esc(u.username||'')} · ${esc(u.email)}</span><br><span class="muted">TG: ${esc(u.telegram_id||'не привязан')}</span><br>${u.roles.map(r=>`<span class="badge">${labels[r]||r}</span>`).join('')}</td><td><div class="roles">${roleChecks(u)}</div></td><td><span class="${u.mfa_enabled?'ok':'bad'}">${u.mfa_enabled?'Включена':'Не настроена'}</span><br><span class="muted">Резервных кодов: ${u.recovery_codes_remaining}</span></td><td>${u.is_active?'Активен':'Отключён'}<br><span class="muted">Версия сессий: ${u.session_version}</span></td><td><button onclick="saveRoles(${u.id},this)">Сохранить права</button> <button class="${u.is_active?'danger':'secondary'}" onclick="toggleUser(${u.id},${!u.is_active},this)">${u.is_active?'Отключить':'Включить'}</button> <button class="secondary" onclick="resetPassword(${u.id},this)">Новый пароль</button>${u.roles.includes('superadmin')?' <button class="warning" onclick="resetMfa('+u.id+',this)">Сбросить MFA</button>':''}</td></tr>`).join('')+'</table>'}
function selected(prefix){return roleNames.filter(r=>document.getElementById(prefix+r).checked)}
async function createUser(button){return withButton(button,async()=>{try{await api('/access/users',{method:'POST',body:JSON.stringify({full_name:full_name.value,username:username.value,email:email.value,telegram_id:telegram_id.value,password:password.value,roles:selected('new_')})});feedback(message,'Пользователь создан',true);password.value='';await loadUsers()}catch(e){feedback(message,e.message,false)}})}
async function saveRoles(id,button){return withButton(button,async()=>{try{await api('/access/users/'+id,{method:'PATCH',body:JSON.stringify({roles:selected('r_'+id+'_')})});feedback(usersMessage,'Права пользователя сохранены',true);await loadUsers()}catch(e){feedback(usersMessage,e.message,false)}})}
async function toggleUser(id,value,button){return withButton(button,async()=>{try{await api('/access/users/'+id,{method:'PATCH',body:JSON.stringify({is_active:value})});feedback(usersMessage,value?'Пользователь включён':'Пользователь отключён',true);await loadUsers()}catch(e){feedback(usersMessage,e.message,false)}})}
async function resetPassword(id,button){const p=prompt('Введите новый пароль (минимум 8 символов)');if(!p)return;return withButton(button,async()=>{try{await api('/access/users/'+id,{method:'PATCH',body:JSON.stringify({password:p})});feedback(usersMessage,'Пароль изменён, старые сессии отозваны',true)}catch(e){feedback(usersMessage,e.message,false)}})}
async function resetMfa(id,button){if(!confirm('Сбросить MFA и отозвать все сессии пользователя?'))return;return withButton(button,async()=>{try{await api('/access/users/'+id+'/mfa/reset',{method:'POST'});feedback(usersMessage,'MFA сброшена, активные сессии отозваны',true);await loadUsers()}catch(e){feedback(usersMessage,e.message,false)}})}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
boot().catch(e=>feedback(usersMessage,e.message,false));
</script></body></html>
"""