"""Protect every mounted sensitive staff API, not Python implementation types.

Inspect the canonical ASGI instance served in production, not a second
application factory invocation. The HTTP path+method is the visible authority. It must resolve to exactly
one FastAPI route with its live revocable session Depends guard.
"""
from app.api import (
    access_management, admin, backup_center, consultation_outcomes,
    exports, payment_review_center, security_event_center, workdesk,
)
from app.main import app as canonical_asgi_app
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


def test_mounted_staff_routers_preserve_live_session_gate():
    mounted = _http_routes(canonical_asgi_app)
    checked = 0
    violations = []

    for router, gateway in ROUTERS:
        defined = _http_routes(router)
        assert defined, f"Empty protected router: {router.prefix}"
        for expected in defined:
            requested_methods = set(expected.methods)
            candidates = [
                route for route in mounted
                if route.path == expected.path
                and requested_methods.issubset(set(route.methods))
            ]
            if len(candidates) != 1:
                violations.append(
                    f"{expected.path} [{','.join(sorted(requested_methods))}]: "
                    f"expected exactly one mounted HTTP route, got {len(candidates)}"
                )
                continue

            actual = candidates[0]
            checked += 1
            dependencies = getattr(actual, "dependant", None)
            if dependencies is None:
                violations.append(
                    f"{actual.path}: {type(actual).__name__} lacks FastAPI "
                    "dependency injection (cannot prove security guard)"
                )
                continue
            calls = {dep.call for dep in dependencies.dependencies}
            if gateway not in calls:
                violations.append(
                    f"{actual.path}: {gateway.__name__} not registered"
                )

            if router is exports.router:
                param_names = {p.name for p in dependencies.query_params}
                if param_names & {"token", "access_token", "admin_token", "api_key"}:
                    violations.append(f"{actual.path}: bearer token query parameter")

    assert not violations, (
        f"{len(violations)} staff authorization gaps: "
        + " | ".join(violations[:12])
        + f" | actual_route_types={sorted(set(type(r).__name__ for r in mounted))}"
    )
    assert checked >= 20, f"Only {checked} sensitive HTTP routes verified"
