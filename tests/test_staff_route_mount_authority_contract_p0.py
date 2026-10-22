"""Verify protected staff routers in independent HTTP registrations.

The full suite exercises and mutates test application instances. Independently
register each declared router into a new FastAPI probe, assert all endpoints
inherit the current, revocable session gateway, and forbid bearer query tokens.
The canonical running app is exercised by browser/staff E2E separately.
"""
from fastapi import FastAPI

from app.api import (
    access_management, admin, backup_center, consultation_outcomes,
    exports, payment_review_center, security_event_center, workdesk,
)
from app.security.current_admin import (
    current_admin_gateway, current_superadmin_gateway,
)

ROUTERS = (
    (admin.router, current_admin_gateway),
    (exports.router, current_admin_gateway),
    (workdesk.router, current_admin_gateway),
    (payment_review_center.router, current_admin_gateway),
    (consultation_outcomes.router, current_admin_gateway),
    (access_management.router, current_superadmin_gateway),
    (security_event_center.router, current_superadmin_gateway),
    (backup_center.router, current_superadmin_gateway),
)


def _http_routes(router):
    return [
        route for route in router.routes
        if hasattr(route, "endpoint") and hasattr(route, "methods")
        and bool(getattr(route, "methods", None))
    ]


def test_independently_mounted_staff_routers_preserve_live_session_gate():
    checked = 0
    failures = []

    for staff_router, gateway in ROUTERS:
        probe = FastAPI()
        probe.include_router(staff_router)
        registered = _http_routes(probe)
        declared = _http_routes(staff_router)
        assert declared, f"Empty protected router: {staff_router.prefix}"

        for expected in declared:
            matching = [
                r for r in registered
                if r.path == expected.path and set(expected.methods).issubset(set(r.methods))
            ]
            if len(matching) != 1:
                failures.append(
                    f"{expected.path}: expected one registered HTTP endpoint, got {len(matching)}"
                )
                continue
            checked += 1
            route = matching[0]
            tree = getattr(route, "dependant", None)
            if tree is None:
                failures.append(f"{route.path}: missing dependency graph")
                continue
            if gateway not in {node.call for node in tree.dependencies}:
                failures.append(f"{route.path}: missing {gateway.__name__}")

            if staff_router is exports.router:
                bad_params = {p.name for p in tree.query_params} & {
                    "token", "admin_token", "access_token", "api_key"
                }
                if bad_params:
                    failures.append(f"{route.path}: URL bearer token accepted")

    assert checked >= 20, f"Only {checked} sensitive routes verified"
    assert not failures, "; ".join(failures[:15])
