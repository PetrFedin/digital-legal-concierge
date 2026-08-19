"""Single runtime owner for the existing lawyer M1/M2 working surface.

This product router replaces precedence between the historical workspace,
consultation, rejection and UI composite routers. It registers the same shipped
business actions once; no new legal route is introduced.
"""

from fastapi import APIRouter

from app.api.contract_workspace_ui import (
    contract_aware_lawyer_workspace_ui,
    legacy_lawyer_ui_redirect,
)
from app.api.guided_lawyer_ui import guided_client_no_show, guided_workspace_data
from app.api.lawyer_consultation_decision_guard import guarded_complete_consultation
from app.api.lawyer_consultation_desk import consultation_desk_data
from app.api.lawyer_consultation_runtime_ui import lawyer_consultation_runtime_ui
from app.api.lawyer_m1_rejection import router as lawyer_m1_rejection_router
from app.api.lawyer_poa import router as lawyer_poa_router

router = APIRouter(tags=["lawyer-product"])

# Existing M1 legal actions that historically arrived through the rejection UI
# composite remain mounted here as domain action routers.
router.include_router(lawyer_m1_rejection_router)
router.include_router(lawyer_poa_router)

router.add_api_route(
    "/lawyer/ui",
    legacy_lawyer_ui_redirect,
    methods=["GET"],
    name="lawyer_ui_redirect",
)
router.add_api_route(
    "/lawyer/workspace/ui",
    contract_aware_lawyer_workspace_ui,
    methods=["GET"],
    name="lawyer_workspace_ui",
)
router.add_api_route(
    "/lawyer/workspace/data",
    guided_workspace_data,
    methods=["GET"],
    name="lawyer_workspace_data",
)
router.add_api_route(
    "/lawyer/consultation-desk/ui",
    lawyer_consultation_runtime_ui,
    methods=["GET"],
    name="lawyer_consultation_desk_ui",
)
router.add_api_route(
    "/lawyer/consultation-desk/data",
    consultation_desk_data,
    methods=["GET"],
    name="lawyer_consultation_desk_data",
)
router.add_api_route(
    "/lawyer/consultations/{consultation_id}/complete",
    guarded_complete_consultation,
    methods=["POST"],
    name="lawyer_complete_consultation",
)
router.add_api_route(
    "/lawyer/consultations/{consultation_id}/client-no-show",
    guided_client_no_show,
    methods=["POST"],
    name="lawyer_client_no_show",
)

__all__ = ["router"]
