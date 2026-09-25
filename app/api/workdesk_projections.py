from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.web_admin import _case_row
from app.api.workdesk import (
    CASE_ACTION_TASKS,
    _render_case_action_html,
    workdesk_attention as legacy_workdesk_attention,
)
from app.config import settings
from app.db.session import get_db
from app.domain.cases.assignment_policy import AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES
from app.domain.cases.case_responsibility import effective_lawyer_ids_for_cases
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor


_CLOSED_CASE_STATUSES = ("M1_CLOSED", "M1_SELF_FILING_CLOSED", "M2_CLOSED", "ARCHIVED")


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


async def _lawyer_names(db: AsyncSession, lawyer_ids: set[int]) -> dict[int, str]:
    if not lawyer_ids:
        return {}
    lawyers = list(
        (
            await db.execute(select(Lawyer).where(Lawyer.id.in_(lawyer_ids)))
        ).scalars().all()
    )
    return {int(item.id): item.full_name for item in lawyers}


async def _m2_responsibility_by_case(
    db: AsyncSession,
    cases: list[Case],
) -> dict[int, tuple[int | None, str]]:
    """Project the operational M2 owner from consultation/slot responsibility.

    M2 responsibility must never be inferred from the M1 ``assigned_lawyer_id``
    field. The result is intentionally a projection; it does not mutate Case.
    """

    m2_cases = [case for case in cases if str(case.route or "") == "M2"]
    if not m2_cases:
        return {}
    responsibility = await effective_lawyer_ids_for_cases(db, m2_cases)
    lawyer_ids = {
        int(lawyer_id)
        for lawyer_id in responsibility.values()
        if lawyer_id is not None
    }
    names = await _lawyer_names(db, lawyer_ids)
    return {
        int(case.id): (
            int(responsibility[int(case.id)])
            if responsibility.get(int(case.id)) is not None
            else None,
            names.get(
                int(responsibility[int(case.id)]),
                "Ответственный по выбранному слоту",
            )
            if responsibility.get(int(case.id)) is not None
            else "Определится по выбранному слоту",
        )
        for case in m2_cases
    }


async def _load_cases(db: AsyncSession, case_ids: list[int]) -> list[Case]:
    normalized = [case_id for case_id in case_ids if case_id > 0]
    if not normalized:
        return []
    return list(
        (
            await db.execute(select(Case).where(Case.id.in_(normalized)))
        ).scalars().all()
    )


async def guarded_active_work_queue(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Return exactly the active matters counted by the Workdesk overview."""

    gate = await _admin_ui_gate(request, db, x_admin_token)
    if isinstance(gate, RedirectResponse):
        return gate
    cases = list(
        (
            await db.execute(
                select(Case)
                .where(Case.status.notin_(_CLOSED_CASE_STATUSES))
                .order_by(Case.updated_at.desc(), Case.id.desc())
                .limit(200)
            )
        ).scalars().all()
    )
    m1_lawyer_ids = {
        int(case.assigned_lawyer_id)
        for case in cases
        if str(case.route or "") != "M2" and case.assigned_lawyer_id is not None
    }
    m1_names = await _lawyer_names(db, m1_lawyer_ids)
    m2_owners = await _m2_responsibility_by_case(db, cases)
    assignment_statuses = set(AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES)
    items: list[dict[str, object]] = []
    for case in cases:
        if str(case.route or "") == "M2":
            lawyer_id, lawyer_name = m2_owners.get(
                int(case.id),
                (None, "Определится по выбранному слоту"),
            )
            row = _case_row(case, queue="active", lawyer_name=lawyer_name)
            row["lawyer_id"] = lawyer_id
            row["next_action"] = case.next_action or (
                "Проверить актуальное состояние консультации"
                if lawyer_id is not None
                else "Ожидать выбора клиентом даты и времени"
            )
        else:
            row = _case_row(
                case,
                queue="active",
                lawyer_name=m1_names.get(int(case.assigned_lawyer_id or 0)),
            )
            if (
                case.assigned_lawyer_id is None
                and str(case.status) not in assignment_statuses
            ):
                row["next_action"] = (
                    case.next_action or "Ожидать следующий шаг клиента"
                )
        items.append(row)
    return {
        "queue": "active",
        "count": len(items),
        "items": items,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def guarded_workdesk_attention(
    request: Request,
    limit: int = 12,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Show the consultation/slot owner on M2 attention cards."""

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


async def guarded_workdesk_case_action(
    case_id: int,
    task: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Serve an existing Workdesk action shell through personal staff auth."""

    if task not in CASE_ACTION_TASKS:
        raise HTTPException(status_code=404, detail="Неизвестное действие по делу")
    gate = await _admin_ui_gate(request, db, x_admin_token)
    if isinstance(gate, RedirectResponse):
        return gate
    return HTMLResponse(_render_case_action_html(case_id))


__all__ = [
    "guarded_active_work_queue",
    "guarded_workdesk_attention",
    "guarded_workdesk_case_action",
]
