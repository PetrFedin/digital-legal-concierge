from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.access_management import (
    ACCESS_HTML,
    create_user as legacy_create_user,
    update_user as legacy_update_user,
)
from app.config import settings
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_LAWYER,
    ROLE_SUPERADMIN,
    normalize_roles,
)
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["access-role-guard"])

PRODUCT_WORKSPACE_ROLES = frozenset({ROLE_ADMIN, ROLE_SUPERADMIN, ROLE_LAWYER})


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


def _has_product_role_conflict(roles: list[str]) -> bool:
    return ROLE_LAWYER in roles and (
        ROLE_ADMIN in roles or ROLE_SUPERADMIN in roles
    )


def _validate_product_workspace_role(payload: dict, *, required: bool) -> None:
    if "roles" not in payload and "role" not in payload:
        if required:
            raise HTTPException(400, "Назначьте базовую роль сотрудника")
        return
    raw = payload.get("roles", payload.get("role"))
    roles = normalize_roles(raw)
    if not roles:
        raise HTTPException(400, "Назначьте хотя бы одну допустимую роль")
    if not PRODUCT_WORKSPACE_ROLES.intersection(roles):
        raise HTTPException(
            400,
            (
                "Роли «Оператор» и «Тестировщик» являются дополнительными техническими ролями. "
                "Чтобы учётная запись не оказалась без рабочего кабинета, добавьте базовую роль "
                "«Администратор» или «Юрист»."
            ),
        )

    # Product workspaces intentionally have different responsibility models:
    # admin operates queues/financial controls, lawyer owns legal facts and case
    # actions. Combining those product roles in one personal account makes the
    # canonical landing and authorization actor ambiguous (admin precedence can
    # silently hide the lawyer workspace). Keep technical roles additive, but
    # require separate personal accounts for admin and lawyer responsibilities.
    if _has_product_role_conflict(roles):
        raise HTTPException(
            400,
            (
                "Нельзя совмещать роли «Администратор/Суперадминистратор» и «Юрист» "
                "в одной рабочей учётной записи. Создайте отдельные персональные учётные "
                "записи для административной и юридической ответственности."
            ),
        )


async def _require_superadmin_ui(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    token = _token(request, header_token)
    try:
        actor = await resolve_document_actor(db, token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return None
        raise
    if actor.role != ROLE_SUPERADMIN:
        raise HTTPException(
            status_code=403,
            detail="Управление пользователями доступно только суперадминистратору",
        )
    return actor


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


def _conflict_warning_html(conflicts: list[AdminUser]) -> str:
    if not conflicts:
        return ""
    items = "".join(
        "<li>"
        f"<b>#{int(user.id)} {escape(user.full_name or user.username or 'Сотрудник')}</b> "
        f"— {escape(user.username or 'без логина')} · "
        f"{escape(user.email or 'без email')}"
        "</li>"
        for user in conflicts
    )
    return (
        '<div class="card" style="border-color:#f59e0b;background:#fffbeb">'
        '<h2 style="margin-top:0">⚠️ Требуется разделить конфликтующие роли</h2>'
        '<p>Найдены исторические учётные записи, где одновременно назначены административная '
        'и юридическая роли. Такой профиль имеет неоднозначный рабочий кабинет и аудит ответственности. '
        'Создайте отдельный персональный профиль юриста и оставьте административную роль только '
        'на административной учётной записи.</p>'
        f'<ul>{items}</ul>'
        '<p class="muted">Система не меняет эти исторические записи автоматически, чтобы не отозвать '
        'доступ и не переназначить юридическую ответственность без решения суперадминистратора.</p>'
        '</div>'
    )


def _guarded_access_html(conflicts: list[AdminUser]) -> str:
    note = (
        '<p class="muted"><b>Базовая рабочая роль обязательна.</b> '
        '«Оператор» и «Тестировщик» можно использовать только как дополнительные технические роли '
        'вместе с «Администратор» или «Юрист». Административную и юридическую ответственность '
        'не совмещайте в одной учётной записи: для роли юриста используйте отдельный персональный вход. '
        'Так маршрутизация кабинетов и аудит действий остаются однозначными.'
        '</p>'
    )
    anchor = '<p class="muted">Для каждого суперадминистратора MFA обязательна и настраивается при первом входе.</p>'
    html = ACCESS_HTML.replace(anchor, note + anchor)
    warning = _conflict_warning_html(conflicts)
    if warning:
        html = html.replace("<main>", "<main>" + warning, 1)
    return html


@router.get("/access/ui", response_class=HTMLResponse)
async def access_ui_guard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _require_superadmin_ui(request, db, x_admin_token)
    if actor is None:
        return RedirectResponse(url="/login", status_code=303)
    conflicts = await _historical_role_conflicts(db)
    return HTMLResponse(_guarded_access_html(conflicts))


@router.post("/access/users")
async def create_user_role_guard(
    request: Request,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    _validate_product_workspace_role(payload, required=True)
    return await legacy_create_user(
        payload=payload,
        db=db,
        x_admin_token=_token(request, x_admin_token),
    )


@router.patch("/access/users/{user_id}")
async def update_user_role_guard(
    user_id: int,
    request: Request,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    _validate_product_workspace_role(payload, required=False)
    return await legacy_update_user(
        user_id=user_id,
        payload=payload,
        db=db,
        x_admin_token=_token(request, x_admin_token),
    )
