"""Ensure privileged routes preserve live authorization even when mounted separately."""
from fastapi.routing import APIRoute
from app.main import create_app
from app.security.current_admin import current_admin_gateway, current_superadmin_gateway

GATES = {
 "app.api.admin": current_admin_gateway,
 "app.api.exports": current_admin_gateway,
 "app.api.workdesk": current_admin_gateway,
 "app.api.payment_review_center": current_admin_gateway,
 "app.api.consultation_outcomes": current_admin_gateway,
 "app.api.access_management": current_superadmin_gateway,
 "app.api.security_event_center": current_superadmin_gateway,
 "app.api.backup_center": current_superadmin_gateway,
}

def test_mounted_staff_routers_preserve_live_session_gate():
    observed = set()
    violations = []
    for route in create_app().routes:
        if not isinstance(route, APIRoute):
            continue
        module = route.endpoint.__module__
        gate = GATES.get(module)
        if gate is None:
            continue
        observed.add(module)
        if gate not in {item.call for item in route.dependant.dependencies}:
            violations.append(f"{route.path} lacks live gate")
        if module == "app.api.exports":
            if {item.name for item in route.dependant.query_params} & {"token", "access_token", "admin_token", "api_key"}:
                violations.append(f"{route.path} allows bearer URL parameter")
    assert observed == set(GATES), f"Missing mounted modules: {set(GATES) - observed}"
    assert not violations, "; ".join(violations)
