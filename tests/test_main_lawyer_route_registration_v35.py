from fastapi.routing import iter_route_contexts

from app.main import create_app


def test_lawyer_workspaces_and_m1_claim_routes_are_registered_in_create_app():
    app = create_app()
    routes = {
        route.path: set(route.methods or ())
        for route in iter_route_contexts(app.routes)
    }

    assert "/lawyer/workspace/ui" in routes
    assert "/lawyer/workspace/data" in routes
    assert "/lawyer/consultation-desk/ui" in routes
    assert "/lawyer/consultation-desk/data" in routes

    assert routes["/lawyer/cases/{case_id}/claim/start"] == {"POST"}
    assert routes["/lawyer/cases/{case_id}/claim/sent"] == {"POST"}
    assert routes["/lawyer/cases/{case_id}/court/open"] == {"POST"}
    assert routes["/lawyer/cases/{case_id}/court/payment/open"] == {"POST"}


def test_lawyer_routes_are_registered_once_without_parallel_demo_endpoints():
    app = create_app()
    paths = [route.path for route in iter_route_contexts(app.routes)]

    for path in {
        "/lawyer/workspace/ui",
        "/lawyer/workspace/data",
        "/lawyer/consultation-desk/ui",
        "/lawyer/consultation-desk/data",
        "/lawyer/cases/{case_id}/claim/start",
        "/lawyer/cases/{case_id}/claim/sent",
        "/lawyer/cases/{case_id}/court/open",
        "/lawyer/cases/{case_id}/court/payment/open",
    }:
        assert paths.count(path) == 1
