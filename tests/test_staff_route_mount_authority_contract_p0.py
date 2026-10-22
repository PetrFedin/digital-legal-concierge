"""Check registered staff endpoints, not Python wrapper metadata.

FastAPI may wrap endpoint callables. Import every protected router object
and compare its (path, methods) against the final application route map.
A missing, duplicate or unguarded route fails the CI release gate.
"""
from fastapi.routing import APIRoute

from app.api import (
    access_management,
    admin,
    backup_center,
    consultation_outcomes,
    exports,
    payment_review_center,
    security_event_center,
    workdesk,
)
from app.main import create_app
from app.security.current_admin import current_admin_gateway, current_superadmin_gateway

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


def test_mounted_staff_routers_preserve_live_session_gate():
    application = create_app()
    mounted = [r for r in application.routes if isinstance(r, APIRoute)]
    checked = 0
    violations = []

    for router, gate in ROUTERS:
        defined = [r for r in router.routes if isinstance(r, APIRoute)]
        assert defined, f"Expected staff router is empty: {router.prefix}"
        for expected in defined:
            # FastAPI may normalize methods or names on cloning routers.
            # The original endpoint callable must still be mounted exactly once.
            candidates = [
                r for r in mounted
                if r.endpoint is expected.endpoint
            ]
            if len(candidates) != 1:
                route_samples = [(r.path, sorted(r.methods or [])) for r in mounted[:5]]
                violations.append(
                    f"{expected.path}: endpoint mounted {len(candidates)} times; "
                    f"samples={route_samples}"
                )
                continue
            actual = candidates[0]
            if actual.path != expected.path:
                violations.append(
                    f"{expected.path}: mounted at unexpected path {actual.path}"
                )
            if not (expected.methods or set()).issubset(actual.methods or set()):
                violations.append(
                    f"{expected.path}: methods differ "
                    f"{sorted(expected.methods or [])} -> {sorted(actual.methods or [])}"
                )
            checked += 1
            if gate not in {d.call for d in actual.dependant.dependencies}:
                violations.append(f"{actual.path} lacks required live session gate")

            if router is exports.router:
                query_keys = {q.name for q in actual.dependant.query_params}
                if query_keys & {"token", "admin_token", "access_token", "api_key"}:
                    violations.append(f"{actual.path} accepts token in URL")

    assert not violations, " | ".join(violations)
    assert checked >= 20, f"Only {checked} staff routes were found"
