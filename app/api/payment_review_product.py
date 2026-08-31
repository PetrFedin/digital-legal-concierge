"""Single runtime owner for the existing M2 payment-review staff surface.

All responses owned by this product router are staff-only operational data.  The
route class therefore applies ``Cache-Control: no-store`` at the final response
boundary instead of relying on every endpoint (including explicit error
responses) to remember the header independently.
"""

from fastapi import APIRouter, Request, Response
from fastapi.routing import APIRoute

from app.api.payment_review_center import (
    list_available_review_slots,
    list_payment_reviews,
    resolve_payment_review,
)
from app.api.payment_review_history import get_payment_review_history
from app.api.staff_ui_guards import protected_payment_review_ui


class NoStoreAPIRoute(APIRoute):
    """Keep every Payment Review response out of browser/proxy caches."""

    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def non_cacheable_handler(request: Request) -> Response:
            response = await route_handler(request)
            response.headers["Cache-Control"] = "no-store"
            return response

        return non_cacheable_handler


router = APIRouter(
    prefix="/admin/payment-reviews",
    tags=["admin", "payment-reviews"],
    route_class=NoStoreAPIRoute,
)
router.add_api_route("", list_payment_reviews, methods=["GET"], name="list_payment_reviews")
router.add_api_route(
    "/slots",
    list_available_review_slots,
    methods=["GET"],
    name="list_available_review_slots",
)
router.add_api_route(
    "/{payment_id}/history",
    get_payment_review_history,
    methods=["GET"],
    name="get_payment_review_history",
)
router.add_api_route(
    "/{payment_id}/resolve",
    resolve_payment_review,
    methods=["POST"],
    name="resolve_payment_review",
)
router.add_api_route(
    "/ui",
    protected_payment_review_ui,
    methods=["GET"],
    name="payment_review_center_ui",
)

__all__ = ["NoStoreAPIRoute", "router"]
