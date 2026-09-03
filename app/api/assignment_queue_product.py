"""Single runtime owner for the existing Workdesk assignment projections.

The legacy ``assignment_queue`` module remains the implementation library. Its
router is deliberately not mounted by the application assembly; this product
router registers only the three data endpoints that belong to assignment/
consultation responsibility. ``GET /admin/workdesk/ui`` is owned by
``workdesk_product`` so route precedence is no longer part of correctness.
"""

from fastapi import APIRouter

from app.api.assignment_queue import (
    actionable_unassigned_queue,
    consultation_queue_with_slot_lawyer,
    workdesk_case_responsibility,
)

router = APIRouter(tags=["admin-assignment-queue"])

router.add_api_route(
    "/admin/workdesk/cases/{case_id}/responsibility",
    workdesk_case_responsibility,
    methods=["GET"],
    name="workdesk_case_responsibility",
)
router.add_api_route(
    "/admin/work-queues/unassigned",
    actionable_unassigned_queue,
    methods=["GET"],
    name="actionable_unassigned_queue",
)
router.add_api_route(
    "/admin/work-queues/consultations",
    consultation_queue_with_slot_lawyer,
    methods=["GET"],
    name="consultation_queue_with_slot_lawyer",
)

__all__ = ["router"]
