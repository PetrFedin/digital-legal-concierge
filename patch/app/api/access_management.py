from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.models.lawyer import Lawyer
from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_LAWYER,
    ROLE_SUPERADMIN,
    VALID_ROLES,
    hash_password,
    normalize_roles,
    serialize_roles,
    verify_access_token,
)

router = APIRouter(prefix="/access", tags=["access-management"])


def require_superadmin(token: str | None) -> None:
    if not verify_access_token(token, ROLE_SUPERADMIN):
        raise HTTPException(status_code=403, detail="Доступ только для суперадминистратора")


def parse_roles(payload: dict, current: str | None = None) -> list[str]:
    raw = payload.get("roles", payload.get("role", current))
    roles = normalize_roles(raw)
    if not roles:
        raise HTTPException(400, "Назначьте хотя бы одну роль")
    if any(role not in VALID_ROLES for role in roles):
        raise HTTPException(400, "Недопустимая роль")
    return roles


async def sync_lawyer(db: AsyncSession, user: AdminUser, roles: list[str]) -> None:
    lawyer = None
    if user.email:
        lawyer = (await db.execute(select(Lawyer).where(Lawyer.email == user.email))).scalars().first()
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


@router.get("/users")
async def list_users(db: AsyncSession = Depends(get_db), x_admin_token: str | None = Header(default=None)):
    require_superadmin(x_admin_token)
    rows = (await db.execute(select(AdminUser).order_by(AdminUser.id.asc()))).scalars().all()
    return [
        {
            "id": u.id,
            "full_name": u.full_name,
            "username": u.username,
            "email": u.email,
            "telegram_id": u.telegram_id,
            "roles": normalize_roles(u.role),
            "role": u.role,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in rows
    ]


@router.post("/users")
async def create_user(payload: dict, db: AsyncSession = Depends(get_db), x_admin_token: str | None = Header(default=None)):
    require_superadmin(x_admin_token)
    username = str(payload.get("username") or "").strip()
    email = str(payload.get("email") or "").strip().lower()
    full_name = str(payload.get("full_name") or "").strip()
    password = str(payload.get("password") or "")
    roles = parse_roles(payload)
    if not username or not email or not full_name:
        raise HTTPException(400, "Имя, логин и email обязательны")
    if (await db.execute(select(AdminUser).where(or_(AdminUser.username == username, AdminUser.email == email)))).scalars().first():
        raise HTTPException(409, "Логин или email уже используется")
    telegram_id = payload.get("telegram_id")
    user = AdminUser(
        full_name=full_name,
        username=username,
        email=email,
        telegram_id=int(telegram_id) if telegram_id not in (None, "") else None,
        password_hash=hash_password(password),
        role=serialize_roles(roles),
        is_active=bool(payload.get("is_active", True)),
    )
    db.add(user)
    await db.flush()
    await sync_lawyer(db, user, roles)
    await db.commit()
    await db.refresh(user)
    return {"ok": True, "id": user.id, "username": user.username, "roles": roles}


@router.patch("/users/{user_id}")
async def update_user(user_id: int, payload: dict, db: AsyncSession = Depends(get_db), x_admin_token: str | None = Header(default=None)):
    require_superadmin(x_admin_token)
    user = (await db.execute(select(AdminUser).where(AdminUser.id == user_id))).scalars().first()
    if not user:
        raise HTTPException(404, "Пользователь не найден")
    roles = parse_roles(payload, user.role) if "roles" in payload or "role" in payload else normalize_roles(user.role)
    if "roles" in payload or "role" in payload:
        user.role = serialize_roles(roles)
    if "is_active" in payload:
        user.is_active = bool(payload["is_active"])
    if payload.get("full_name"):
        user.full_name = str(payload["full_name"]).strip()
    if "telegram_id" in payload:
        user.telegram_id = int(payload["telegram_id"]) if payload["telegram_id"] not in (None, "") else None
    if payload.get("password"):
        user.password_hash = hash_password(str(payload["password"]))
    await sync_lawyer(db, user, roles)
    await db.commit()
    return {"ok": True, "id": user.id, "roles": roles, "is_active": user.is_active}


@router.get("/ui", response_class=HTMLResponse)
async def access_ui():
    return HTMLResponse(ACCESS_HTML)


ACCESS_HTML = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Пользователи и права</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f4f5f7;margin:0;color:#111827}header{background:#111827;color:#fff;padding:18px 24px;display:flex;justify-content:space-between;align-items:center}main{padding:24px;max-width:1200px;margin:auto}.card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:16px}input{width:100%;padding:10px;border:1px solid #d1d5db;border-radius:9px;box-sizing:border-box}label{font-size:13px;font-weight:700}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.roles{display:flex;gap:14px;flex-wrap:wrap;padding:12px 0}.roles label{display:flex;align-items:center;gap:6px}.roles input{width:auto}button{border:0;border-radius:9px;padding:10px 13px;background:#2563eb;color:#fff;font-weight:700;cursor:pointer}.danger{background:#dc2626}.secondary{background:#4b5563}table{width:100%;border-collapse:collapse}td,th{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}.muted{color:#6b7280;font-size:13px}.badge{display:inline-block;background:#e5e7eb;border-radius:999px;padding:4px 8px;margin:2px;font-size:12px}@media(max-width:700px){.grid{grid-template-columns:1fr}table{font-size:12px}}</style></head>
<body><header><b>⚖ Пользователи, роли и юристы</b><div><a href="/admin-ui" style="color:#fff">Админка</a></div></header><main>
<div class="card"><h2>Добавить пользователя</h2><div class="grid"><div><label>ФИО</label><input id="full_name"></div><div><label>Логин</label><input id="username"></div><div><label>Email</label><input id="email" type="email"></div><div><label>Telegram ID (необязательно)</label><input id="telegram_id"></div><div><label>Пароль (минимум 8 символов)</label><input id="password" type="password"></div></div><h3>Права</h3><div class="roles"><label><input type="checkbox" id="new_admin" checked> Администратор</label><label><input type="checkbox" id="new_superadmin"> Суперадминистратор</label><label><input type="checkbox" id="new_lawyer"> Юрист</label></div><p class="muted">Права можно совмещать. Например: «Юрист» + «Администратор».</p><p><button onclick="createUser()">Создать пользователя</button></p><div id="message" class="muted"></div></div>
<div class="card"><h2>Текущие пользователи</h2><div id="users">Загрузка…</div></div></main>
<script>
let token=''; const labels={admin:'Администратор',superadmin:'Суперадминистратор',lawyer:'Юрист'};
async function boot(){const r=await fetch('/auth/session');if(!r.ok){location.href='/login';return}const s=await r.json();if(!(s.roles||[s.role]).includes('superadmin')){document.body.innerHTML='<main><div class="card"><h2>Недостаточно прав</h2><p>Этот раздел доступен только суперадминистратору.</p><a href="/admin-ui">Вернуться</a></div></main>';return}token=s.api_token;await loadUsers()}
async function api(path,opts={}){const r=await fetch(path,{...opts,headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка');return d}
function roleChecks(u){return ['admin','superadmin','lawyer'].map(r=>`<label><input type="checkbox" id="r_${u.id}_${r}" ${u.roles.includes(r)?'checked':''}> ${labels[r]}</label>`).join('')}
async function loadUsers(){const rows=await api('/access/users');users.innerHTML='<table><tr><th>Пользователь</th><th>Права</th><th>Статус</th><th>Действия</th></tr>'+rows.map(u=>`<tr><td><b>${esc(u.full_name)}</b><br><span class="muted">${esc(u.username||'')} · ${esc(u.email)}</span><br>${u.roles.map(r=>`<span class="badge">${labels[r]||r}</span>`).join('')}</td><td><div class="roles">${roleChecks(u)}</div></td><td>${u.is_active?'Активен':'Отключён'}</td><td><button onclick="saveRoles(${u.id})">Сохранить права</button> <button class="${u.is_active?'danger':'secondary'}" onclick="toggleUser(${u.id},${!u.is_active})">${u.is_active?'Отключить':'Включить'}</button> <button class="secondary" onclick="resetPassword(${u.id})">Новый пароль</button></td></tr>`).join('')+'</table>'}
function selected(prefix){return ['admin','superadmin','lawyer'].filter(r=>document.getElementById(prefix+r).checked)}
async function createUser(){try{await api('/access/users',{method:'POST',body:JSON.stringify({full_name:full_name.value,username:username.value,email:email.value,telegram_id:telegram_id.value,password:password.value,roles:selected('new_')})});message.textContent='Пользователь создан';password.value='';await loadUsers()}catch(e){message.textContent=e.message}}
async function saveRoles(id){try{await api('/access/users/'+id,{method:'PATCH',body:JSON.stringify({roles:selected('r_'+id+'_')})});await loadUsers()}catch(e){alert(e.message)}}
async function toggleUser(id,value){await api('/access/users/'+id,{method:'PATCH',body:JSON.stringify({is_active:value})});await loadUsers()}
async function resetPassword(id){const p=prompt('Введите новый пароль (минимум 8 символов)');if(!p)return;await api('/access/users/'+id,{method:'PATCH',body:JSON.stringify({password:p})});alert('Пароль изменён')}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
boot();
</script></body></html>
"""
