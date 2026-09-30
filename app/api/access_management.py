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
        "account_version": int(user.account_version or 1),
    }


def _require_expected_account_version(user: AdminUser, payload: dict) -> int:
    try:
        expected = int(payload.get("expected_account_version"))
    except (TypeError, ValueError):
        raise HTTPException(
            409,
            "Экран устарел: обновите учётную запись перед изменением доступа",
        )
    current = int(user.account_version or 1)
    if expected != current:
        raise HTTPException(
            409,
            "Учётная запись уже изменена другим действием. Обновите данные и повторите решение",
        )
    return expected


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
        account_version=1,
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
    _require_expected_account_version(user, payload)

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
    user.account_version = int(user.account_version or 1) + 1

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
        "account_version": int(user.account_version or 1),
    }


@router.post("/users/{user_id}/mfa/reset")
async def reset_user_mfa(
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
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, "Пользователь не найден")
    _require_expected_account_version(user, payload)
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
    user.account_version = int(user.account_version or 1) + 1
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
    return {
        "ok": True,
        "user_id": int(user.id),
        "mfa_setup_required": True,
        "account_version": int(user.account_version or 1),
    }


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
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Пользователи и права</title>
<style>
:root{--bg:#f4f6fa;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--red:#b42318;--amber:#a15c00;--green:#166534}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}header{background:#111827;color:#fff;padding:18px 24px;display:flex;justify-content:space-between;gap:12px;align-items:center}header a{color:#fff}header p{margin:4px 0 0;color:#d0d5dd;font-size:13px}main{max-width:1160px;margin:auto;padding:20px}.card,.warning{background:#fff;border:1px solid var(--line);border-radius:16px;padding:17px;margin-bottom:14px}.warning{border-color:#fedf89;background:#fffbeb;color:#7a2e0e}.section-label{font-size:11px;font-weight:850;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:7px}.summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.metric{border:1px solid var(--line);border-radius:12px;padding:11px}.metric b{display:block;font-size:22px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}label{font-size:13px;font-weight:700}input{width:100%;margin-top:5px;padding:10px;border:1px solid #d0d5dd;border-radius:9px}.roles{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}.roles label{display:flex;align-items:center;gap:5px}.roles input{width:auto;margin:0}button{border:0;border-radius:9px;padding:9px 11px;background:var(--blue);color:#fff;font-weight:750;cursor:pointer}.secondary{background:#fff;color:var(--ink);border:1px solid var(--line)}.danger{background:var(--red)}button:disabled{opacity:.55;cursor:wait}.muted{color:var(--muted);font-size:13px;line-height:1.45}.user{border-top:1px solid var(--line);padding:12px 0}.user:first-child{border-top:0}.chips{display:flex;gap:5px;flex-wrap:wrap;margin:6px 0}.chip{background:#eef2ff;border-radius:999px;padding:4px 7px;font-size:12px}.conflict{color:var(--red);font-weight:750}.good{color:var(--green)}details{margin-top:9px;border:1px solid var(--line);border-radius:12px;padding:10px}summary{cursor:pointer;font-weight:750}.edit-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:10px}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.notice{padding:11px 12px;border-radius:10px;background:#f5f8ff;border:1px solid #c7d7fe;margin-top:10px}.bad{color:var(--red)}@media(max-width:760px){.grid,.edit-grid,.summary{grid-template-columns:1fr 1fr}}@media(max-width:560px){.grid,.edit-grid,.summary{grid-template-columns:1fr}header{align-items:flex-start;flex-direction:column}main{padding:12px}.actions button{width:100%}}
</style>
</head>
<body>
<header><div><b>⚖ Пользователи, роли и MFA</b><p id="identity">Роль: суперадминистратор · управление персональными учётными записями</p></div><div><a href="/audit-center/ui">Аудит</a> · <a href="/operator">Рабочее пространство</a></div></header>
<main>
__ROLE_WARNING__
<section class="card"><div class="section-label">Сейчас</div><div id="summary" class="summary"><div class="metric"><b>—</b><span class="muted">загрузка пользователей</span></div></div><div id="mainStep" class="notice"><b>Главный следующий шаг</b><div class="muted">Проверяем состав ролей и доступность суперадминистраторов.</div></div></section>
<section class="card"><div class="section-label">Добавить сотрудника</div><p class="muted">Одна персональная учётная запись — одна базовая продуктовая ответственность. Технические роли можно добавлять дополнительно.</p><div class="grid"><label>ФИО<input id="full_name"></label><label>Логин<input id="username"></label><label>Email<input id="email" type="email"></label><label>Telegram ID<input id="telegram_id"></label><label>Временный пароль<input id="password" type="password" autocomplete="new-password"></label></div><div class="roles"><label><input type="checkbox" id="new_admin" checked>Администратор</label><label><input type="checkbox" id="new_superadmin">Суперадминистратор</label><label><input type="checkbox" id="new_lawyer">Юрист</label><label><input type="checkbox" id="new_operator">Оператор</label><label><input type="checkbox" id="new_tester">Тестировщик</label></div><button onclick="createUser(this)">Создать пользователя</button><p id="message" class="muted"></p></section>
<section class="card"><div class="section-label">Текущие пользователи</div><p class="muted">Изменение ролей, статуса или пароля отзывает активные сессии. MFA суперадминистратора сбрасывается только отдельным подтверждённым действием.</p><div id="users">Загрузка…</div></section>
</main>
<script>
let token='',sessionUsername='',rowsById=new Map();
const roleIds={admin:'new_admin',superadmin:'new_superadmin',lawyer:'new_lawyer',operator:'new_operator',tester:'new_tester'};
const roleLabels={admin:'Администратор',superadmin:'Суперадминистратор',lawyer:'Юрист',operator:'Оператор',tester:'Тестировщик'};
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function boot(){const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();if(!(s.roles||[]).includes('superadmin')){location.href='/operator';return}token=s.api_token||'';sessionUsername=String(s.username||'');identity.textContent=`Роль: суперадминистратор · ${sessionUsername||'персональная сессия'} · управление доступом`;await loadUsers()}
async function api(path,options={}){const headers={'x-admin-token':token,'content-type':'application/json',...(options.headers||{})};const r=await fetch(path,{credentials:'same-origin',cache:'no-store',...options,headers});let d={};try{d=await r.json()}catch(e){}if(r.status===401){location.href='/login';throw new Error('Сессия завершена')}if(!r.ok)throw new Error(d.detail||'Ошибка запроса');return d}
function selectedRoles(){return Object.entries(roleIds).filter(([,id])=>document.getElementById(id).checked).map(([role])=>role)}
function editRoleIds(id){return Object.keys(roleLabels).map(role=>`role-${id}-${role}`)}
function selectedEditRoles(id){return Object.keys(roleLabels).filter(role=>document.getElementById(`role-${id}-${role}`)?.checked)}
async function createUser(btn){btn.disabled=true;const msg=document.getElementById('message');msg.textContent='Сохраняем…';try{const payload={full_name:full_name.value.trim(),username:username.value.trim(),email:email.value.trim(),telegram_id:telegram_id.value.trim()||null,password:password.value,roles:selectedRoles()};const result=await api('/access/users',{method:'POST',body:JSON.stringify(payload)});msg.textContent=result.mfa_setup_required?'Пользователь создан. При первом входе суперадминистратор должен настроить MFA.':'Пользователь создан.';full_name.value='';username.value='';email.value='';telegram_id.value='';password.value='';await loadUsers()}catch(e){msg.textContent=e.message}finally{btn.disabled=false}}
function roleEditor(u){return Object.entries(roleLabels).map(([role,label])=>`<label><input type="checkbox" id="role-${u.id}-${role}" ${u.roles.includes(role)?'checked':''}>${esc(label)}</label>`).join('')}
function renderUser(u){const self=String(u.username||'')===sessionUsername;const conflict=u.role_conflict?'<div class="conflict">Конфликт продуктовых ролей — требуется разделить административную и юридическую ответственность</div>':'';const mfa=(u.roles||[]).includes('superadmin')?`<span>MFA: ${u.mfa_enabled?'включена':'требует настройки'} · recovery-кодов: ${esc(u.recovery_codes_remaining)}</span>`:'<span>MFA для этой роли не требуется</span>';return `<article class="user"><b>#${u.id} ${esc(u.full_name||u.username)}${self?' · вы':''}</b>${conflict}<div class="chips">${(u.roles||[]).map(x=>`<span class="chip">${esc(roleLabels[x]||x)}</span>`).join('')}</div><div class="muted">${esc(u.username||'')} · ${esc(u.email||'')} · ${u.is_active?'активен':'отключён'} · ${mfa}</div><details><summary>Управлять учётной записью</summary><div class="edit-grid"><label>ФИО<input id="name-${u.id}" value="${esc(u.full_name||'')}"></label><label>Telegram ID<input id="tg-${u.id}" value="${esc(u.telegram_id||'')}"></label><label>Новый пароль, если нужен<input id="pw-${u.id}" type="password" autocomplete="new-password" placeholder="не менять"></label><label style="display:flex;align-items:center;gap:8px;margin-top:24px"><input id="active-${u.id}" type="checkbox" style="width:auto;margin:0" ${u.is_active?'checked':''}>Учётная запись активна</label></div><div class="roles">${roleEditor(u)}</div><div class="notice muted">Логин и email здесь не переименовываются. Изменение ролей, активности или пароля сразу отзывает прежние сессии.</div><div class="actions"><button onclick="saveUser(${u.id},this)">Сохранить изменения</button>${(u.roles||[]).includes('superadmin')?`<button class="danger" onclick="resetMfa(${u.id},this)">Сбросить MFA</button>`:''}</div><div id="user-msg-${u.id}" class="muted"></div></details></article>`}
function updateSummary(rows){const active=rows.filter(x=>x.is_active).length;const supers=rows.filter(x=>x.is_active&&(x.roles||[]).includes('superadmin')).length;const lawyers=rows.filter(x=>x.is_active&&(x.roles||[]).includes('lawyer')).length;const conflicts=rows.filter(x=>x.role_conflict).length;summary.innerHTML=`<div class="metric"><b>${rows.length}</b><span class="muted">учётных записей</span></div><div class="metric"><b>${active}</b><span class="muted">активны</span></div><div class="metric"><b>${supers}</b><span class="muted">активных superadmin</span></div><div class="metric"><b>${conflicts}</b><span class="muted">конфликтов ролей</span></div>`;mainStep.innerHTML=conflicts?'<b>Главный следующий шаг</b><div class="bad">Разделите конфликтующие административные и юридические роли. Система не меняет их автоматически.</div>':supers<1?'<b>Главный следующий шаг</b><div class="bad">Нет активного суперадминистратора. Не отключайте текущую сессию и восстановите резервный доступ.</div>':`<b>Главный следующий шаг</b><div class="good">Критических конфликтов ролей не обнаружено. Поддерживайте минимум один доступный профиль суперадминистратора; активных юристов: ${lawyers}.</div>`}
async function loadUsers(){const rows=await api('/access/users');rowsById=new Map(rows.map(u=>[Number(u.id),u]));users.innerHTML=rows.map(renderUser).join('')||'<p class="muted">Пользователей нет.</p>';updateSummary(rows)}
async function saveUser(id,btn){const u=rowsById.get(Number(id));if(!u)return;const msg=document.getElementById(`user-msg-${id}`);const roles=selectedEditRoles(id);const isActive=document.getElementById(`active-${id}`).checked;const password=document.getElementById(`pw-${id}`).value;const rolesChanged=JSON.stringify([...roles].sort())!==JSON.stringify([...(u.roles||[])].sort());const activeChanged=isActive!==Boolean(u.is_active);if((rolesChanged||activeChanged||password)&&!confirm(`Изменить доступ для ${u.full_name||u.username}? Активные сессии будут отозваны.`))return;btn.disabled=true;msg.textContent='Сохраняем…';try{const payload={full_name:document.getElementById(`name-${id}`).value.trim(),telegram_id:document.getElementById(`tg-${id}`).value.trim()||null,roles,is_active:isActive,expected_account_version:Number(u.account_version)};if(password)payload.password=password;const result=await api(`/access/users/${id}`,{method:'PATCH',body:JSON.stringify(payload)});if(result.sessions_revoked&&String(u.username||'')===sessionUsername){alert('Ваши права или пароль изменены. Текущая сессия отозвана — войдите заново.');location.href='/login';return}msg.textContent=result.sessions_revoked?'Изменения сохранены. Прежние сессии пользователя отозваны.':'Изменения сохранены.';await loadUsers()}catch(e){msg.textContent=e.message}finally{btn.disabled=false}}
async function resetMfa(id,btn){const u=rowsById.get(Number(id));if(!u)return;if(!confirm(`Сбросить MFA для ${u.full_name||u.username}? Все его активные сессии будут отозваны, при следующем входе потребуется новая настройка.`))return;btn.disabled=true;const msg=document.getElementById(`user-msg-${id}`);msg.textContent='Сбрасываем MFA…';try{await api(`/access/users/${id}/mfa/reset`,{method:'POST',body:JSON.stringify({expected_account_version:Number(u.account_version)})});if(String(u.username||'')===sessionUsername){alert('MFA вашей учётной записи сброшена. Текущая сессия отозвана — войдите и настройте MFA заново.');location.href='/login';return}msg.textContent='MFA сброшена. При следующем входе пользователь настроит её заново.';await loadUsers()}catch(e){msg.textContent=e.message}finally{btn.disabled=false}}
boot().catch(e=>{users.textContent=e.message});
</script>
</body>
</html>
"""

__all__ = [
    "PRODUCT_WORKSPACE_ROLES",
    "parse_roles",
    "router",
]
