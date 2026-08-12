from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.api.guided_message_center import _inject_message_center_patch
from app.api.message_center import MESSAGE_CENTER_HTML

router = APIRouter(tags=["message-center-role-ui"])

_BAD_DOCUMENT_LINK = (
    "const caseId=Number(c.id),documents=localHref("
    "'/admin/workdesk/cases/'+caseId+'/action/documents');"
)
_ROLE_SAFE_DOCUMENT_LINK = (
    "const caseId=Number(c.id),documents=localHref("
    "'/document-access/ui?case_id='+caseId);"
)


def role_safe_message_center_html() -> str:
    """Render the guided message center with a document link safe for all roles.

    The admin workdesk document action is intentionally admin-only. Lawyers who
    own an M2 consultation must use the personal-session document portal, whose
    authorization boundary resolves M1 assignment vs M2 consultation ownership.
    """

    html = _inject_message_center_patch(MESSAGE_CENTER_HTML)
    if html.count(_BAD_DOCUMENT_LINK) != 1:
        raise RuntimeError(
            "Message center template contract changed: admin-only document link not found"
        )
    return html.replace(_BAD_DOCUMENT_LINK, _ROLE_SAFE_DOCUMENT_LINK, 1)


@router.get("/message-center/ui", response_class=HTMLResponse)
async def role_safe_message_center_ui():
    return HTMLResponse(role_safe_message_center_html())
