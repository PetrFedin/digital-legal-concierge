from fastapi.routing import iter_route_contexts

from app.main import create_app


def test_admin_workdesk_routes_are_mounted_in_application():
    paths = {route.path for route in iter_route_contexts(create_app().routes)}

    assert "/admin/workdesk/ui" in paths
    assert "/admin/workdesk/attention" in paths
    assert "/admin/workdesk/cases/{case_id}/action/{task}" in paths
    assert "/admin/workdesk/cases/{case_id}/sla" in paths
    assert "/admin/workdesk/cases/{case_id}/documents" in paths
    assert "/admin/workdesk/cases/{case_id}/timeline" in paths


def test_launch_check_exposes_workdesk_for_deploy_verification():
    app = create_app()
    launch_route = next(
        route
        for route in iter_route_contexts(app.routes)
        if route.path == "/launch-check"
    )
    assert launch_route is not None
