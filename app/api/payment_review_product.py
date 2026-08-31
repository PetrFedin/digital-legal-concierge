"""Single runtime owner for the existing M2 payment-review staff surface."""

from fastapi import APIRouter

from app.api.payment_review_center import (
    list_available_review_slots,
    list_payment_reviews,
    resolve_payment_review,
)
from app.api.payment_review_history import get_payment_review_history
from app.api.staff_ui_guards import protected_payment_review_ui

router = APIRouter(prefix="/admin/payment-reviews", tags=["admin", "payment-reviews"])
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

__all__ = ["router"]
