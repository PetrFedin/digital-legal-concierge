"""Single runtime owner for the existing staff SLA surface."""

from fastapi import APIRouter

from app.api.sla_center import acknowledge_sla, list_sla_cases, run_sla_check
from app.api.staff_ui_guards import protected_sla_ui

router = APIRouter(prefix="/admin/sla", tags=["admin", "sla"])
router.add_api_route("", list_sla_cases, methods=["GET"], name="list_sla_cases")
router.add_api_route(
    "/{case_id}/acknowledge",
    acknowledge_sla,
    methods=["POST"],
    name="acknowledge_sla",
)
router.add_api_route("/run", run_sla_check, methods=["POST"], name="run_sla_check")
router.add_api_route("/ui", protected_sla_ui, methods=["GET"], name="sla_center_ui")

__all__ = ["router"]
