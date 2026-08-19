"""Single runtime owner for the Workdesk process-integrity API.

The mature integrity projection was historically layered through
``initial_setup_wizard -> workdesk_integrity_guard -> workdesk_integrity``.
That made a daily staff endpoint depend on compatibility assembly order. This
module is now the only router registered by the application for the public path;
the existing guard function remains the implementation source while the legacy
containers are retired incrementally.
"""

from fastapi import APIRouter

from app.api.workdesk_integrity_guard import workdesk_integrity_guard

router = APIRouter(tags=["admin-workdesk-integrity"])
router.add_api_route(
    "/admin/workdesk/integrity",
    workdesk_integrity_guard,
    methods=["GET"],
    name="workdesk_integrity",
)

__all__ = ["router"]
