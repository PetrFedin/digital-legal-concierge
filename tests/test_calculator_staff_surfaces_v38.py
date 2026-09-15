from app.api.calculator_builder import router as calculator_builder_router
from app.api.workdesk_product import router as workdesk_router
from app.api.workdesk_runtime_ui import render_workdesk_runtime_html


def _routes(router):
    return {
        (method, route.path)
        for route in router.routes
        for method in getattr(route, "methods", set())
    }


def test_calculation_rule_admin_surface_has_controlled_lifecycle_routes():
    routes = _routes(calculator_builder_router)

    assert ("GET", "/calculator-builder/status") in routes
    assert ("GET", "/calculator-builder/rules") in routes
    assert ("GET", "/calculator-builder/ui") in routes
    assert ("POST", "/calculator-builder/draft") in routes
    assert ("POST", "/calculator-builder/{revision_id}/edit") in routes
    assert ("POST", "/calculator-builder/{revision_id}/approve") in routes
    assert ("POST", "/calculator-builder/{revision_id}/retire") in routes


def test_workdesk_mounts_case_bound_calculator_evidence_projection():
    routes = _routes(workdesk_router)

    assert (
        "GET",
        "/admin/workdesk/cases/{case_id}/calculator",
    ) in routes


def test_workdesk_case_card_renders_inputs_separately_from_latest_result():
    html = render_workdesk_runtime_html()

    assert "Исходные данные клиента" in html
    assert "Последний предварительный расчёт" in html
    assert "/admin/workdesk/cases/'+id+'/calculator" in html
    # Routine staff UI may expose the revision/hash identifier but never the
    # full reproducibility snapshot payload itself.
    assert "rule_snapshot_json" not in html
    assert "applied_segments" not in html
