from __future__ import annotations

import inspect

from fastapi.routing import APIRoute

from app.api import payment_review_product
from app.api.payment_review_product import NoStoreAPIRoute


def test_every_payment_review_product_route_uses_no_store_boundary() -> None:
    routes = [
        route
        for route in payment_review_product.router.routes
        if isinstance(route, APIRoute)
    ]

    assert payment_review_product.router.route_class is NoStoreAPIRoute
    assert routes
    assert all(isinstance(route, NoStoreAPIRoute) for route in routes)
    assert {route.name for route in routes} == {
        "list_payment_reviews",
        "list_available_review_slots",
        "get_payment_review_history",
        "resolve_payment_review",
        "payment_review_center_ui",
    }


def test_no_store_boundary_covers_returned_responses_and_http_exceptions() -> None:
    source = inspect.getsource(NoStoreAPIRoute.get_route_handler)

    assert "response = await route_handler(request)" in source
    assert 'response.headers["Cache-Control"] = "no-store"' in source
    assert "except StarletteHTTPException as exc:" in source
    assert "headers = dict(exc.headers or {})" in source
    assert 'headers["Cache-Control"] = "no-store"' in source
    assert "exc.headers = headers" in source
    assert source.count("raise") == 1


def test_payment_review_product_surface_stays_narrow_and_method_bounded() -> None:
    actual = {
        (route.path, frozenset(route.methods or set()))
        for route in payment_review_product.router.routes
        if isinstance(route, APIRoute)
    }

    assert actual == {
        ("/admin/payment-reviews", frozenset({"GET"})),
        ("/admin/payment-reviews/slots", frozenset({"GET"})),
        (
            "/admin/payment-reviews/{payment_id}/history",
            frozenset({"GET"}),
        ),
        (
            "/admin/payment-reviews/{payment_id}/resolve",
            frozenset({"POST"}),
        ),
        ("/admin/payment-reviews/ui", frozenset({"GET"})),
    }
