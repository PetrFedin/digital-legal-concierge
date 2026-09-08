from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.guided_message_center import _inject_message_center_patch
from app.api.message_center import MESSAGE_CENTER_HTML, require_staff_scope
from app.config import settings
from app.db.session import get_db

router = APIRouter(tags=["message-center-role-ui"])

_BAD_DOCUMENT_LINK = (
    "const caseId=Number(c.id),documents=localHref("
    "'/admin/workdesk/cases/'+caseId+'/action/documents');"
)
_ROLE_SAFE_DOCUMENT_LINK = (
    "const caseId=Number(c.id),documents=localHref("
    "'/document-access/review/ui?case_id='+encodeURIComponent(caseId));"
)
_BASE_SCRIPT_MARKER = "<script>\nlet currentCaseId=null,currentLatestMessageId=null"
_RAW_MESSAGE_TIME = "${esc(m.created_at||'')}"
_STAFF_IDENTITY = (
    "document.getElementById('staff').textContent=`${s.username} · "
    "${(s.roles||[]).join(', ')}`;"
)


def _js_string(value: object) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False).replace("<", "\\u003c")


def _inject_business_timezone_ui(html: str) -> str:
    """Keep Message Center timestamps independent from the staff browser zone."""

    if html.count(_BASE_SCRIPT_MARKER) != 1:
        raise RuntimeError(
            "Message center template contract changed: base script marker not found"
        )
    if html.count(_RAW_MESSAGE_TIME) != 1:
        raise RuntimeError(
            "Message center template contract changed: raw message timestamp not found"
        )
    if html.count(_STAFF_IDENTITY) != 1:
        raise RuntimeError(
            "Message center template contract changed: staff identity renderer not found"
        )

    zone = _js_string(settings.business_timezone)
    label = _js_string(settings.business_timezone_label)
    bootstrap = f"""<script>
const businessTimeZone={zone},businessTimeLabel={label};
function formatBusinessTime(value){{
  if(!value)return '—';
  try{{
    const rendered=new Intl.DateTimeFormat('ru-RU',{{dateStyle:'short',timeStyle:'short',timeZone:businessTimeZone}}).format(new Date(value));
    return businessTimeLabel?rendered+' '+businessTimeLabel:rendered;
  }}catch(_){{return String(value)}}
}}
let currentCaseId=null,currentLatestMessageId=null"""
    html = html.replace(_BASE_SCRIPT_MARKER, bootstrap, 1)
    html = html.replace(
        _RAW_MESSAGE_TIME,
        "${esc(formatBusinessTime(m.created_at))}",
        1,
    )
    html = html.replace(
        _STAFF_IDENTITY,
        "document.getElementById('staff').textContent=`${s.username} · ${(s.roles||[]).join(', ')} · Время: ${businessTimeLabel||businessTimeZone}`;",
        1,
    )
    return html


def role_safe_message_center_html() -> str:
    """Render the guided message center with role-safe links and one time zone.

    The base template and guided patch still duplicate the legacy admin-only
    document action. Normalize every composed occurrence to the canonical
    role-aware review surface so a lawyer's M2 responsibility is resolved by
    consultation/slot ownership rather than by an M1 case assignment. The
    duplicate source ownership remains migration debt until both source owners
    can be collapsed safely.

    All visible message timestamps are formatted in the configured business
    timezone rather than whichever timezone the browser happens to use.
    """

    html = _inject_message_center_patch(MESSAGE_CENTER_HTML)
    if _BAD_DOCUMENT_LINK not in html:
        raise RuntimeError(
            "Message center template contract changed: admin-only document link not found"
        )
    html = html.replace(_BAD_DOCUMENT_LINK, _ROLE_SAFE_DOCUMENT_LINK)
    return _inject_business_timezone_ui(html)


@router.get("/message-center/ui", response_class=HTMLResponse)
async def role_safe_message_center_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Serve the shared message-center shell only to an authenticated staff role."""

    try:
        await require_staff_scope(request, db, x_admin_token)
    except HTTPException as exc:
        if exc.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        if exc.status_code in {403, 409}:
            return RedirectResponse(url="/admin-ui", status_code=303)
        raise
    return HTMLResponse(role_safe_message_center_html())
