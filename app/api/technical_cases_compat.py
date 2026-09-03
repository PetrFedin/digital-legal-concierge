"""Explicit compatibility owner for the retired technical-cases UI URL.

The destination is the canonical Workdesk. Keeping this one redirect preserves
old bookmarks without making the initial-setup/operator compatibility chain own
unrelated staff product routes.
"""

from fastapi import APIRouter

from app.api.staff_ui_guards import retired_technical_cases_ui

router = APIRouter(tags=["staff-compat"])
router.add_api_route(
    "/admin/technical-cases/ui",
    retired_technical_cases_ui,
    methods=["GET"],
    name="retired_technical_cases_ui",
)

__all__ = ["router"]
