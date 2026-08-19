"""Single runtime owner for the existing daily Workdesk surface.

The legacy ``workdesk`` and ``assignment_queue`` modules remain implementation
libraries. Their routers are deliberately not mounted by application assembly.
This module registers the existing Workdesk read/action screens exactly once and
uses the personally-authenticated guided UI implementation already shipped for
M1/M2 operations. No new Workdesk capability is introduced.
"""

from fastapi import APIRouter

from app.api.assignment_queue import guided_workdesk_ui
from app.api.workdesk import (
    workdesk_attention,
    workdesk_case_action,
    workdesk_case_consultation_outcomes,
    workdesk_case_consultations_today,
    workdesk_case_documents,
    workdesk_case_sla,
)

router = APIRouter(tags=["admin", "workdesk"])

router.add_api_route(
    "/admin/workdesk/attention",
    workdesk_attention,
    methods=["GET"],
    name="workdesk_attention",
)
router.add_api_route(
    "/admin/workdesk/cases/{case_id}/documents",
    workdesk_case_documents,
    methods=["GET"],
    name="workdesk_case_documents",
)
router.add_api_route(
    "/admin/workdesk/cases/{case_id}/consultation-outcomes",
    workdesk_case_consultation_outcomes,
    methods=["GET"],
    name="workdesk_case_consultation_outcomes",
)
router.add_api_route(
    "/admin/workdesk/cases/{case_id}/consultations-today",
    workdesk_case_consultations_today,
    methods=["GET"],
    name="workdesk_case_consultations_today",
)
router.add_api_route(
    "/admin/workdesk/cases/{case_id}/sla",
    workdesk_case_sla,
    methods=["GET"],
    name="workdesk_case_sla",
)
router.add_api_route(
    "/admin/workdesk/cases/{case_id}/action/{task}",
    workdesk_case_action,
    methods=["GET"],
    name="workdesk_case_action",
)
router.add_api_route(
    "/admin/workdesk/ui",
    guided_workdesk_ui,
    methods=["GET"],
    name="workdesk_ui",
)

__all__ = ["router"]
