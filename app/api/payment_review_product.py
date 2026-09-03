"""Single runtime owner for the existing M2 payment-review staff surface.

Payment Review contains staff-only operational data.  The route class applies
``Cache-Control: no-store`` at the final response boundary and also carries the
same directive through HTTP exceptions raised by handlers or dependencies.
"""

from fastapi import APIRouter, Request, Response
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.payment_review_center import (
    list_available_review_slots,
    list_payment_reviews,
    resolve_payment_review,
)
from app.api.payment_review_history import get_payment_review_history
from app.api.staff_ui_guards import protected_payment_review_ui


class NoStoreAPIRoute(APIRoute):
    """Keep Payment Review handler and HTTP-exception responses out of caches."""

    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def non_cacheable_handler(request: Request) -> Response:
            try:
                response = await route_handler(request)
            except StarletteHTTPException as exc:
                headers = dict(exc.headers or {})
                headers["Cache-Control"] = "no-store"
                exc.headers = headers
                raise
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
