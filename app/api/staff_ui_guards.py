from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.document_review import REVIEW_HTML
from app.api.payment_review_center import PAYMENT_REVIEW_CENTER_HTML
from app.api.sla_center import SLA_CENTER_HTML
from app.config import settings
from app.db.session import get_db
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.payment import Payment
from app.security.access_control import ROLE_ADMIN, ROLE_LAWYER, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["staff-ui-guards"])
_M2_OPEN_LINK_STATUSES = {
    PaymentStatus.PENDING.value,
    PaymentStatus.WAITING_CONFIRMATION.value,
}
_RAW_BROWSER_DT_RENDERERS = (
    "function dt(v){return v?new Date(v).toLocaleString('ru-RU'):'—'}",
    "function dt(v){return v?new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short'}).format(new Date(v)):'—'}",
)
_SLA_CARD_HEAD = '<div class="case-head"><div><h3>${esc(x.case_number)}</h3>'
_SLA_GUIDED_CARD_HEAD = (
    '<div class="case-head"><div><div class="rule"><b>Сейчас</b></div>'
    '<h3>${esc(x.case_number)}</h3>'
)
_SLA_NEXT_LABEL = "<b>Следующий шаг по делу</b>"
_SLA_GUIDED_NEXT_LABEL = "<b>Главный следующий шаг</b>"


def _js_string(value: object) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False).replace("<", "\\u003c")


def _inject_business_timezone_ui(html: str) -> str:
    """Render operational timestamps in one server-configured business zone.

    Document Review and SLA used the staff browser timezone, so the same UTC
    instant could be displayed differently on two workstations. Their templates
    currently use two equivalent local-time ``dt`` implementations; harden
    either at the authenticated product boundary instead of duplicating the
    large HTML templates.
    """

    matches = [renderer for renderer in _RAW_BROWSER_DT_RENDERERS if renderer in html]
    if len(matches) != 1:
        raise RuntimeError(
            "Staff UI template contract changed: expected exactly one browser-local dt renderer"
        )
    zone = _js_string(settings.business_timezone)
    label = _js_string(settings.business_timezone_label)
    replacement = (
        f"const businessTimeZone={zone},businessTimeLabel={label};"
        "function dt(v){"
        "if(!v)return '—';"
        "try{const rendered=new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short',timeZone:businessTimeZone}).format(new Date(v));"
        "return businessTimeLabel?rendered+' '+businessTimeLabel:rendered}"
        "catch(_){return String(v)}"
        "}"
    )
    return html.replace(matches[0], replacement, 1)


def _inject_sla_guided_copy(html: str) -> str:
    """Use the same context -> now -> main step hierarchy as other staff UIs."""

    if html.count(_SLA_CARD_HEAD) != 1 or html.count(_SLA_NEXT_LABEL) != 1:
        raise RuntimeError("SLA UI template contract changed: guided-card markers not found")
    return html.replace(_SLA_CARD_HEAD, _SLA_GUIDED_CARD_HEAD, 1).replace(
        _SLA_NEXT_LABEL,
        _SLA_GUIDED_NEXT_LABEL,
        1,
    )


async def _actor(request: Request, db: AsyncSession, header_token: str | None):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    return await resolve_document_actor(db, token)


async def _admin(request: Request, db: AsyncSession, header_token: str | None):
    actor = await _actor(request, db, header_token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


async def _staff(request: Request, db: AsyncSession, header_token: str | None):
    actor = await _actor(request, db, header_token)
    if actor.role not in {ROLE_LAWYER, ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для юридической команды")
    return actor


async def _staff_gate(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
    *,
    staff: bool = False,
):
    """Resolve a UI actor without exposing raw JSON authorization dead ends."""

    try:
        actor = (
            await _staff(request, db, header_token)
            if staff
            else await _admin(request, db, header_token)
        )
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        if error.status_code in {403, 409}:
            return RedirectResponse(url="/admin-ui", status_code=303)
        raise
    except HTTPException as error:
        if error.status_code in {403, 409}:
            return RedirectResponse(url="/admin-ui", status_code=303)
        raise
    return actor


async def _guarded_html(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
    html: str,
    *,
    staff: bool = False,
):
    """Authenticate first; template injections must never weaken the gate."""

    gate = await _staff_gate(
        request,
        db,
        header_token,
        staff=staff,
    )
    if isinstance(gate, RedirectResponse):
        return gate
    return HTMLResponse(html)


# Implementation helpers for explicit product routers. They deliberately have
# no decorators here; runtime ownership lives in app.api.*_product.
async def protected_payment_review_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    gate = await _staff_gate(request, db, x_admin_token)
    if isinstance(gate, RedirectResponse):
        return gate

    raw_payment_id = str(request.query_params.get("payment_id") or "").strip()
    try:
        payment_id = int(raw_payment_id) if raw_payment_id else None
    except ValueError:
        payment_id = None
    if payment_id and payment_id > 0:
        payment = await db.get(Payment, payment_id)
        if (
            payment is not None
            and str(payment.payment_code) == PaymentCode.M2_CONSULTATION_PAYMENT.value
            and str(payment.status) in _M2_OPEN_LINK_STATUSES
        ):
            return RedirectResponse(
                url=(
                    f"/admin/workdesk/payments/{int(payment.id)}/reconcile-m2-reservation/ui"
                    f"?case_id={int(payment.case_id)}"
                ),
                status_code=303,
            )

    # Payment Review already reads business_timezone from /auth/session and owns
    # its own formatter; injecting a second formatter would create competing UI
    # sources of truth.
    return HTMLResponse(PAYMENT_REVIEW_CENTER_HTML)


async def protected_sla_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    html = _inject_business_timezone_ui(SLA_CENTER_HTML)
    html = _inject_sla_guided_copy(html)
    return await _guarded_html(
        request,
        db,
        x_admin_token,
        html,
    )


async def protected_document_review_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Serve the document decision cabinet only after personal staff auth."""

    return await _guarded_html(
        request,
        db,
        x_admin_token,
        _inject_business_timezone_ui(REVIEW_HTML),
        staff=True,
    )


@router.get("/admin/technical-cases/ui")
async def retired_technical_cases_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Retire the old technical-case destination into the canonical Workdesk."""

    gate = await _staff_gate(request, db, x_admin_token)
    if isinstance(gate, RedirectResponse):
        return gate

    raw_case_id = str(request.query_params.get("case_id") or "").strip()
    try:
        case_id = int(raw_case_id) if raw_case_id else None
    except ValueError:
        case_id = None
    target = (
        f"/admin/workdesk/ui?case_id={case_id}"
        if case_id is not None and case_id > 0
        else "/admin/workdesk/ui"
    )
    return RedirectResponse(url=target, status_code=303)


__all__ = [
    "protected_document_review_ui",
    "protected_payment_review_ui",
    "protected_sla_ui",
    "router",
]
