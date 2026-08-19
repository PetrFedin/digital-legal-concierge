"""Single runtime owner for the existing daily Workdesk surface.

Historically ``workdesk_ui_guard`` had to be mounted before ``workdesk`` so its
hardened UI won the duplicate ``GET /admin/workdesk/ui`` route. Daily staff
operations must not depend on FastAPI include order. This router owns the
already-shipped Workdesk API and UI paths while reusing the current business
handlers; no new Workdesk capability is introduced.
"""

from fastapi import APIRouter

from app.api.workdesk import (
    case_action_history,
    cleanup_integrity_history,
    list_workdesk_cases,
    update_case,
    workdesk_filters,
)
from app.api.workdesk_ui_guard import protected_workdesk_ui

router = APIRouter(prefix="/admin/workdesk", tags=["admin", "workdesk"])

router.add_api_route("", list_workdesk_cases, methods=["GET"], name="list_workdesk_cases")
router.add_api_route("/filters", workdesk_filters, methods=["GET"], name="workdesk_filters")
router.add_api_route(
    "/cases/{case_id}/action-history",
    case_action_history,
    methods=["GET"],
    name="case_action_history",
)
router.add_api_route(
    "/cases/{case_id}",
    update_case,
    methods=["PATCH"],
    name="update_case",
)
router.add_api_route(
    "/cases/{case_id}/cleanup-integrity-history",
    cleanup_integrity_history,
    methods=["GET"],
    name="cleanup_integrity_history",
)
router.add_api_route(
    "/ui",
    protected_workdesk_ui,
    methods=["GET"],
    name="workdesk_ui",
)

__all__ = ["router"]
