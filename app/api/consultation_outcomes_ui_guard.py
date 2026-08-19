from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.consultation_outcomes import (
    OUTCOMES_HTML,
    router as canonical_outcomes_router,
)
from app.api.guided_consultation_outcomes import (
    _inject_client_no_show_ui,
    router as guided_outcomes_router,
)
from app.api.legacy_consultation_outcome_guard import (
    inject_legacy_outcome_ui,
    router as legacy_consultation_outcome_router,
)
from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["consultation-outcomes-ui-guard"])


def _retire_shadow_ui_routes() -> None:
    """Make this guarded, patched UI the single runtime owner of the path.

    Historical canonical/guided modules both published the same public /ui
    endpoint. FastAPI would then choose behaviour by include order. Remove those
    shadow routes before application assembly and fail closed if the expected
    compatibility contract changes.
    """

    target_path = "/admin/consultation-outcomes/ui"
    removed = 0
    for source_router in (canonical_outcomes_router, guided_outcomes_router):
        matches = [
            route
            for route in source_router.routes
            if getattr(route, "path", None) == target_path
            and "GET" in set(getattr(route, "methods", set()) or set())
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "Consultation outcomes UI compatibility route contract changed"
            )
        target = matches[0]
        source_router.routes[:] = [
            route for route in source_router.routes if route is not target
        ]
        removed += 1
    if removed != 2:
        raise RuntimeError("Consultation outcomes shadow UI routes were not retired")


_retire_shadow_ui_routes()

# Legacy recovery APIs remain unique business endpoints. They are mounted here
# only until the staff consultation-outcomes package is physically consolidated;
# unlike the retired UI routes, they do not shadow any canonical method/path.
router.include_router(legacy_consultation_outcome_router)


@router.get("/admin/consultation-outcomes/ui", response_class=HTMLResponse)
async def consultation_outcomes_ui_guard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
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
