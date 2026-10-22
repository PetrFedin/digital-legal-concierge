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
            candidates = [
                r for r in mounted
                if r.path == expected.path and r.methods == expected.methods
            ]
            if len(candidates) != 1:
                violations.append(
                    f"{expected.path}: {len(candidates)} mounted endpoints instead of one"
                )
                continue
            actual = candidates[0]
            checked += 1
            if gate not in {d.call for d in actual.dependant.dependencies}:
                violations.append(f"{actual.path} lacks required live session gate")

            if router is exports.router:
                query_keys = {q.name for q in actual.dependant.query_params}
                if query_keys & {"token", "admin_token", "access_token", "api_key"}:
                    violations.append(f"{actual.path} accepts token in URL")

    assert checked >= 20, f"Only {checked} staff routes were found"
    assert not violations, " | ".join(violations)
