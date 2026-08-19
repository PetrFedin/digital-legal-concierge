"""Single runtime owner for the existing daily Workdesk surface.

Legacy modules remain implementation libraries and are not mounted by the app.
The product router owns the shipped Workdesk URLs exactly once and deliberately
uses the hardened personal-session implementations where browser navigation or
M2 responsibility require them. No new business capability is introduced.
"""

from fastapi import APIRouter

from app.api.assignment_queue import guided_workdesk_ui
from app.api.workdesk import (
    workdesk_case_consultation_outcomes,
    workdesk_case_consultations_today,
    workdesk_case_documents,
    workdesk_case_sla,
)
from app.api.workdesk_ui_guard import (
    guarded_active_work_queue,
    guarded_workdesk_attention,
    guarded_workdesk_case_action,
)

router = APIRouter(tags=["admin", "workdesk"])

router.add_api_route(
    "/admin/workdesk/attention",
    guarded_workdesk_attention,
    methods=["GET"],
    name="workdesk_attention",
)
router.add_api_route(
    "/admin/work-queues/active",
    guarded_active_work_queue,
    methods=["GET"],
    name="active_work_queue",
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
    guarded_workdesk_case_action,
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
