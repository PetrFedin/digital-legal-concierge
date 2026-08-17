from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.web_admin import (
    _case_row,
    case_workspace as legacy_case_workspace,
    work_queue as legacy_work_queue,
)
from app.api.workdesk import (
    CASE_ACTION_TASKS,
    _render_case_action_html,
    workdesk_attention as legacy_workdesk_attention,
)
from app.api.workdesk_ui import WORKDESK_HTML
from app.config import settings
from app.db.session import get_db
from app.domain.cases.assignment_policy import AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES
from app.domain.cases.case_responsibility import effective_lawyer_ids_for_cases
from app.models.case import Case
from app.models.lawyer import Lawyer
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

_M1_SLA_SHORTCUT = r"""slaShortcut=d.case.lawyer_id?`<a class="button secondary" href="/admin/workdesk/cases/${id}/action/sla">SLA</a>`:`<button class="secondary" onclick="assign(${id},this)">Назначить перед SLA</button>`"""
_ROUTE_AWARE_SLA_SHORTCUT = r"""slaShortcut=d.case.route==='M2'?`<span class="muted">Ответственный M2 определяется выбранным слотом; M1 SLA здесь не назначается вручную.</span>`:d.case.lawyer_id?`<a class="button secondary" href="/admin/workdesk/cases/${id}/action/sla">SLA</a>`:`<button class="secondary" onclick="assign(${id},this)">Назначить перед SLA</button>`"""


def guided_workdesk_html() -> str:
    """Apply deep-link and route-aware responsibility UX to the Workdesk shell."""

    html = WORKDESK_HTML
    marker = "boot();"
    if html.count(marker) != 1:
        raise RuntimeError("Workdesk template contract changed: boot marker is not unique")
    if html.count(_M1_SLA_SHORTCUT) != 1:
        raise RuntimeError("Workdesk template contract changed: SLA shortcut marker is not unique")
    html = html.replace(_M1_SLA_SHORTCUT, _ROUTE_AWARE_SLA_SHORTCUT, 1)
    return html.replace(marker, _WORKDESK_DEEP_LINK_BOOT, 1)


def _effective_token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _admin_ui_gate(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    token = _effective_token(request, header_token)
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


async def _m2_responsibility_by_case(
    db: AsyncSession,
    cases: list[Case],
) -> dict[int, tuple[int | None, str]]:
    m2_cases = [case for case in cases if str(case.route or "") == "M2"]
    if not m2_cases:
        return {}
    responsibility = await effective_lawyer_ids_for_cases(db, m2_cases)
    lawyer_ids = {
        int(lawyer_id)
        for lawyer_id in responsibility.values()
        if lawyer_id is not None
    }
    names: dict[int, str] = {}
    if lawyer_ids:
        lawyers = list(
            (
                await db.execute(select(Lawyer).where(Lawyer.id.in_(lawyer_ids)))
            ).scalars().all()
        )
        names = {int(item.id): item.full_name for item in lawyers}
    return {
        int(case.id): (
            int(responsibility[int(case.id)])
            if responsibility.get(int(case.id)) is not None
            else None,
            names.get(int(responsibility[int(case.id)]), "Ответственный по выбранному слоту")
            if responsibility.get(int(case.id)) is not None
            else "Определится по выбранному слоту",
        )
        for case in m2_cases
    }


async def _load_cases(db: AsyncSession, case_ids: list[int]) -> list[Case]:
    if not case_ids:
        return []
    return list(
        (
            await db.execute(select(Case).where(Case.id.in_(case_ids)))
        ).scalars().all()
    )


@router.get("/admin/work-queues/unassigned")
async def guarded_unassigned_work_queue(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Show only cases where product policy actually requires M1 assignment."""

    gate = await _admin_ui_gate(request, db, x_admin_token)
    if isinstance(gate, RedirectResponse):
        return gate
    cases = list(
        (
            await db.execute(
                select(Case)
                .where(Case.assigned_lawyer_id.is_(None))
                .where(Case.status.in_(AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES))
                .order_by(Case.created_at.asc(), Case.id.asc())
                .limit(200)
            )
        ).scalars().all()
    )
    return {
        "queue": "unassigned",
        "count": len(cases),
        "items": [
            _case_row(case, queue="unassigned", lawyer_name=None)
            for case in cases
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/admin/work-queues/consultations")
async def guarded_consultation_work_queue(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Use M2 slot ownership in the admin consultation queue, never M1 assignment."""

    gate = await _admin_ui_gate(request, db, x_admin_token)
    if isinstance(gate, RedirectResponse):
        return gate
    payload = await legacy_work_queue(
        queue_name="consultations",
        db=db,
        x_admin_token=_effective_token(request, x_admin_token),
    )
    items = list(payload.get("items") or [])
    cases = await _load_cases(db, [int(item.get("id") or 0) for item in items])
    cases_by_id = {int(case.id): case for case in cases}
    owners = await _m2_responsibility_by_case(db, cases)
    for item in items:
        case_id = int(item.get("id") or 0)
        case = cases_by_id.get(case_id)
        if case is None or str(case.route or "") != "M2":
            continue
        lawyer_id, lawyer_name = owners.get(
            case_id,
            (None, "Определится по выбранному слоту"),
        )
        item["lawyer_id"] = lawyer_id
        item["lawyer_name"] = lawyer_name
        item["next_action"] = case.next_action or "Открыть консультацию и проверить актуальное состояние"
    payload["items"] = items
    return payload


@router.get("/admin/workdesk/attention")
async def guarded_workdesk_attention(
    request: Request,
    limit: int = 12,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Show the slot owner on M2 attention cards without inventing case assignment."""

    gate = await _admin_ui_gate(request, db, x_admin_token)
    if isinstance(gate, RedirectResponse):
        return gate
    payload = await legacy_workdesk_attention(
        limit=limit,
        db=db,
        x_admin_token=_effective_token(request, x_admin_token),
    )
    items = list(payload.get("items") or [])
    cases = await _load_cases(db, [int(item.get("id") or 0) for item in items])
    owners = await _m2_responsibility_by_case(db, cases)
    for item in items:
        case_id = int(item.get("id") or 0)
        if str(item.get("route") or "") != "M2":
            continue
        lawyer_id, lawyer_name = owners.get(
            case_id,
            (None, "Определится по выбранному слоту"),
        )
        item["lawyer_id"] = lawyer_id
        item["lawyer_name"] = lawyer_name
    payload["items"] = items
    return payload


@router.get("/admin/case-workspace/{case_id}")
async def guarded_case_workspace(
    case_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Keep M2 Workdesk cards aligned with consultation-slot responsibility."""

    gate = await _admin_ui_gate(request, db, x_admin_token)
    if isinstance(gate, RedirectResponse):
        return gate
    payload = await legacy_case_workspace(
        case_id=case_id,
        db=db,
        x_admin_token=_effective_token(request, x_admin_token),
    )
    case = await db.get(Case, case_id)
    if case is None or str(case.route or "") != "M2":
        return payload
    owner = (await _m2_responsibility_by_case(db, [case])).get(
        int(case.id),
        (None, "Определится по выбранному слоту"),
    )
    lawyer_id, lawyer_name = owner
    case_payload = payload.get("case") or {}
    case_payload["lawyer_id"] = lawyer_id
    case_payload["lawyer_name"] = lawyer_name
    case_payload["next_action"] = case.next_action or (
        "Проверить актуальное состояние консультации"
        if lawyer_id is not None
        else "Ответственный определится после выбора клиентом времени"
    )
    payload["case"] = case_payload
    return payload


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
