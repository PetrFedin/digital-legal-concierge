from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.consultation_outcomes import (
    OUTCOMES_HTML,
    available_slots,
    mark_lawyer_no_show,
    rebook_after_lawyer_no_show,
    refund_after_lawyer_no_show,
)
from app.api.guided_consultation_outcomes import (
    _inject_client_no_show_ui,
    close_after_client_no_show,
    guided_outcome_queue,
    rebook_after_client_no_show,
)
from app.api.legacy_consultation_outcome_guard import (
    inject_legacy_outcome_ui,
    legacy_outcome_queue,
    resolve_legacy_outcome,
)
from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(
    prefix="/admin/consultation-outcomes",
    tags=["admin", "consultation-outcomes"],
)

# One runtime owner per public path. Existing endpoint functions remain the
# implementation source while this module is the only router mounted by the
# application. This removes FastAPI include-order and import-time route mutation
# without changing the business services or URLs used by staff UI/bookmarks.
router.add_api_route(
    "",
    guided_outcome_queue,
    methods=["GET"],
    name="consultation_outcomes_queue",
)
router.add_api_route(
    "/slots",
    available_slots,
    methods=["GET"],
    name="consultation_outcomes_slots",
)
router.add_api_route(
    "/{consultation_id}/lawyer-no-show",
    mark_lawyer_no_show,
    methods=["POST"],
    name="consultation_outcomes_lawyer_no_show",
)
router.add_api_route(
    "/{consultation_id}/rebook",
    rebook_after_lawyer_no_show,
    methods=["POST"],
    name="consultation_outcomes_lawyer_no_show_rebook",
)
router.add_api_route(
    "/{consultation_id}/refund",
    refund_after_lawyer_no_show,
    methods=["POST"],
    name="consultation_outcomes_lawyer_no_show_refund",
)
router.add_api_route(
    "/{consultation_id}/client-no-show/rebook",
    rebook_after_client_no_show,
    methods=["POST"],
    name="consultation_outcomes_client_no_show_rebook",
)
router.add_api_route(
    "/{consultation_id}/client-no-show/close",
    close_after_client_no_show,
    methods=["POST"],
    name="consultation_outcomes_client_no_show_close",
)
router.add_api_route(
    "/legacy",
    legacy_outcome_queue,
    methods=["GET"],
    name="consultation_outcomes_legacy_queue",
)
router.add_api_route(
    "/{consultation_id}/legacy/resolve",
    resolve_legacy_outcome,
    methods=["POST"],
    name="consultation_outcomes_legacy_resolve",
)


@router.get("/ui", response_class=HTMLResponse, name="consultation_outcomes_ui")
async def consultation_outcomes_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Single authenticated staff UI for every existing M2 outcome decision."""

    token = x_admin_token or request.cookies.get(settings.admin_session_cookie)
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

    html = _inject_client_no_show_ui(OUTCOMES_HTML)
    return HTMLResponse(inject_legacy_outcome_ui(html))


__all__ = ["consultation_outcomes_ui", "router"]
