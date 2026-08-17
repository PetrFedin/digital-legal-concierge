from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.workdesk import CASE_ACTION_TASKS, _render_case_action_html
from app.api.workdesk_ui import WORKDESK_HTML
from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["workdesk-ui-guard"])

_WORKDESK_DEEP_LINK_BOOT = r"""
const _workdeskOpenCase=openCase;
openCase=async function(id){
  const numeric=Number(id);
  const result=await _workdeskOpenCase(numeric);
  if(Number.isInteger(numeric)&&numeric>0&&!cv.querySelector('.error')){
    const url=new URL(window.location.href);
    url.searchParams.set('case_id',String(numeric));
    history.replaceState(null,'',url.pathname+url.search+url.hash);
  }
  return result;
};
const _workdeskCloseCase=closeCase;
closeCase=function(){
  _workdeskCloseCase();
  const url=new URL(window.location.href);
  url.searchParams.delete('case_id');
  history.replaceState(null,'',url.pathname+url.search+url.hash);
};
(async()=>{
  await boot();
  if(!token)return;
  const raw=new URLSearchParams(window.location.search).get('case_id');
  const requested=Number(raw||0);
  if(!Number.isInteger(requested)||requested<=0)return;
  await openCase(requested);
  if(!cv.querySelector('.error')){
    say('Открыто дело из прямой ссылки.','ok');
    cv.scrollIntoView({behavior:'smooth',block:'start'});
  }
})();
""".strip()


def guided_workdesk_html() -> str:
    """Make every existing ?case_id= Workdesk link actually open its case."""

    marker = "boot();"
    if WORKDESK_HTML.count(marker) != 1:
        raise RuntimeError("Workdesk template contract changed: boot marker is not unique")
    return WORKDESK_HTML.replace(marker, _WORKDESK_DEEP_LINK_BOOT, 1)


async def _admin_ui_gate(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    try:
        actor = await resolve_document_actor(db, token)
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
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        return RedirectResponse(url="/admin-ui", status_code=303)
    return actor


@router.get("/admin/workdesk/ui", response_class=HTMLResponse)
async def guarded_workdesk_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    gate = await _admin_ui_gate(request, db, x_admin_token)
    if isinstance(gate, RedirectResponse):
        return gate
    return HTMLResponse(guided_workdesk_html())


@router.get(
    "/admin/workdesk/cases/{case_id}/action/{task}",
    response_class=HTMLResponse,
)
async def guarded_workdesk_case_action(
    case_id: int,
    task: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    if task not in CASE_ACTION_TASKS:
        raise HTTPException(status_code=404, detail="Неизвестное действие по делу")
    gate = await _admin_ui_gate(request, db, x_admin_token)
    if isinstance(gate, RedirectResponse):
        return gate
    return HTMLResponse(_render_case_action_html(case_id))


__all__ = ["guided_workdesk_html", "router"]
