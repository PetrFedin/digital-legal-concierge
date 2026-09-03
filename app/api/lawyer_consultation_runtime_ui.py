from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.guided_lawyer_ui import _CONSULTATION_DRAFT_PATCH
from app.api.lawyer_consultation_desk import CONSULTATION_DESK_HTML
from app.config import settings
from app.db.session import get_db
from app.security.lawyer_access import require_lawyer_actor


_COMPLETION_DECISION_EXTENSION = r"""
<script>
(function(){
  const previousOpenForm=openForm;
  openForm=function(id,type){
    const result=previousOpenForm(id,type);
    if(type!=='complete')return result;
    const form=document.getElementById('form_'+id);
    if(!form)return result;
    const obsolete=form.querySelector('select option[value="other"]');
    if(obsolete)obsolete.remove();
    const help=form.querySelector('.form-help');
    if(help){
      help.textContent='После «В M1» клиент получит документы и следующий шаг полного ведения. «Follow-up» означает дополнительный шаг в рамках консультации. «Закрыть обращение» завершает M2.';
    }
    return result;
  };
})();
</script>
"""


def _append_body_extensions(html: str, *extensions: str) -> str:
    head, marker, tail = html.rpartition("</body>")
    if not marker:
        raise RuntimeError(
            "Lawyer consultation template contract changed: closing body missing"
        )
    return head + "\n" + "\n".join(extensions) + "\n" + marker + tail


def render_lawyer_consultation_html() -> str:
    """Compose existing consultation behavior without exact HTML replacements."""

    return _append_body_extensions(
        CONSULTATION_DESK_HTML,
        _CONSULTATION_DRAFT_PATCH,
        _COMPLETION_DECISION_EXTENSION,
    )


async def lawyer_consultation_runtime_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    token = x_admin_token or request.cookies.get(settings.admin_session_cookie)
    if not token:
        return RedirectResponse(url="/login", status_code=303)
    try:
        await require_lawyer_actor(db, token)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(render_lawyer_consultation_html())


__all__ = [
    "lawyer_consultation_runtime_ui",
    "render_lawyer_consultation_html",
]
